"""Encrypted SQLite checkpoints for a single-worker demo, with bounded retention."""
import asyncio
import base64
import fcntl
import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from time import time

from cryptography.fernet import Fernet, InvalidToken
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from ..paths import PROJECT_ROOT
from .checkpoints import clean_checkpoint, clean_writes, safe_checkpoint_value
from .memory_policy import MAX_CHECKPOINTS, RETENTION_SECONDS


class EncryptedSerializer:
    """Encrypt checkpoint values AND metadata; never silently accept plaintext."""
    def __init__(self, key):
        self.cipher = Fernet(key)
        self.inner = JsonPlusSerializer(pickle_fallback=False)

    def dumps(self, value):
        kind, data = self.inner.dumps_typed(safe_checkpoint_value(value))
        envelope = json.dumps([kind, base64.b64encode(data).decode()]).encode()
        return self.cipher.encrypt(envelope)

    def loads(self, value):
        try:
            kind, data = json.loads(self.cipher.decrypt(value))
        except (InvalidToken, ValueError, TypeError):
            raise RuntimeError("Cannot decrypt conversation storage; check the memory key.") from None
        return self.inner.loads_typed((kind, base64.b64decode(data)))

    def dumps_typed(self, value):
        return "fernet-v1", self.dumps(value)

    def loads_typed(self, value):
        kind, data = value
        if kind != "fernet-v1":
            raise RuntimeError("Unsupported conversation storage format.")
        return self.loads(data)


class PrivacySqliteSaver(SqliteSaver):
    def __init__(self, conn, key, *, max_checkpoints=MAX_CHECKPOINTS, retention=RETENTION_SECONDS):
        serializer = EncryptedSerializer(key)
        super().__init__(conn, serde=serializer)
        self.jsonplus_serde = serializer
        self.operation_lock = RLock()
        self.max_checkpoints = max_checkpoints
        self.retention = retention
        self.file_lock = None
        self.setup()
        with self.cursor() as cur:
            cur.execute("PRAGMA secure_delete=ON")
            cur.execute("CREATE TABLE IF NOT EXISTS memory_entries (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, created REAL NOT NULL, PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id))")
            cur.execute("CREATE TABLE IF NOT EXISTS memory_key_check (id INTEGER PRIMARY KEY, token BLOB NOT NULL)")
            row = cur.execute("SELECT token FROM memory_key_check WHERE id=1").fetchone()
            if row:
                if serializer.loads(row[0]) != "insurance-memory-v1":
                    raise RuntimeError("Invalid conversation storage key.")
            else:
                cur.execute("INSERT INTO memory_key_check VALUES (1, ?)", (serializer.dumps("insurance-memory-v1"),))
        self.cleanup()

    def cleanup(self):
        with self.operation_lock, self.cursor() as cur:
            cutoff = time() - self.retention
            cur.execute("DELETE FROM memory_entries WHERE created <= ?", (cutoff,))
            cur.execute("DELETE FROM checkpoints WHERE NOT EXISTS (SELECT 1 FROM memory_entries m WHERE m.thread_id=checkpoints.thread_id AND m.checkpoint_ns=checkpoints.checkpoint_ns AND m.checkpoint_id=checkpoints.checkpoint_id)")
            cur.execute("DELETE FROM writes WHERE NOT EXISTS (SELECT 1 FROM checkpoints c WHERE c.thread_id=writes.thread_id AND c.checkpoint_ns=writes.checkpoint_ns AND c.checkpoint_id=writes.checkpoint_id)")

    def put(self, config, checkpoint, metadata, new_versions):
        with self.operation_lock:
            result = super().put(config, clean_checkpoint(checkpoint), safe_checkpoint_value(metadata), new_versions)
            thread = str(config["configurable"]["thread_id"])
            ns = config["configurable"].get("checkpoint_ns", "")
            with self.cursor() as cur:
                cur.execute("INSERT OR REPLACE INTO memory_entries VALUES (?, ?, ?, ?)", (thread, ns, checkpoint["id"], time()))
                cur.execute("DELETE FROM memory_entries WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id NOT IN (SELECT checkpoint_id FROM memory_entries WHERE thread_id=? AND checkpoint_ns=? ORDER BY checkpoint_id DESC LIMIT ?)", (thread, ns, thread, ns, self.max_checkpoints))
            self.cleanup()
            return result

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.operation_lock:
            return super().put_writes(config, clean_writes(writes), task_id, task_path)

    def get_tuple(self, config):
        with self.operation_lock:
            self.cleanup()
            return super().get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.operation_lock:
            self.cleanup()
            # SQL JSON filters cannot operate on ciphertext; apply them after decrypting.
            rows = list(super().list(config, before=before, limit=None if filter else limit))
        selected = [row for row in rows if not filter or all(row.metadata.get(k) == v for k, v in filter.items())]
        yield from selected[:limit] if limit is not None else selected

    def delete_thread(self, thread_id):
        with self.operation_lock:
            super().delete_thread(thread_id)
            with self.cursor() as cur:
                cur.execute("DELETE FROM memory_entries WHERE thread_id=?", (str(thread_id),))
            # Flush encrypted WAL records; backups still require their own retention policy.
            with self.lock:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for row in rows:
            yield row

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)

    def close(self):
        with self.operation_lock:
            self.conn.close()
            if self.file_lock is not None:
                os.close(self.file_lock)
                self.file_lock = None


def open_memory(directory=None, key=None):
    directory = Path(directory or os.getenv("MEMORY_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = directory / "conversations.sqlite3"
    key_path = directory / "memory.key"
    # The API's session locks are process-local; fail instead of pretending multi-worker safety.
    lock_fd = os.open(str(directory / "memory.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        raise RuntimeError("Conversation storage is already in use; run one application worker.") from None
    conn = None
    try:
        key = key or os.getenv("MEMORY_ENCRYPTION_KEY")
        if not key:
            if key_path.exists():
                key = key_path.read_bytes().strip()
            elif db_path.exists():
                raise RuntimeError("Conversation key is missing; restore it before opening existing data.")
            else:
                key = Fernet.generate_key()
                fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(key)
        # Validate before opening/writing a database.
        Fernet(key)
        fd = os.open(str(db_path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        saver = PrivacySqliteSaver(conn, key)
        saver.file_lock = lock_fd
        return saver
    except Exception:
        if conn is not None:
            conn.close()
        os.close(lock_fd)
        raise

"""Defense in depth: redact prohibited strings BEFORE any checkpoint serialization."""
from langgraph.checkpoint.memory import MemorySaver
from threading import RLock
from time import time

from ..guardrails.input_guard import detect_sensitive_overcollection, sanitize_user_text
from .memory_policy import MAX_CHECKPOINTS, RETENTION_SECONDS, bounded_messages


def safe_checkpoint_value(value):
    if isinstance(value, str):
        return "[sensitive input withheld]" if detect_sensitive_overcollection(sanitize_user_text(value)) else value
    if isinstance(value, dict):
        return {key: safe_checkpoint_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_checkpoint_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(safe_checkpoint_value(item) for item in value)
    return value


class PrivacyMemorySaver(MemorySaver):
    def __init__(self, *, max_checkpoints=MAX_CHECKPOINTS, retention=RETENTION_SECONDS):
        super().__init__()
        self.max_checkpoints = max_checkpoints
        self.retention = retention
        self.updated = {}
        self.memory_lock = RLock()

    def cleanup(self):
        with self.memory_lock:
            for thread in list(self.updated):
                if time() - self.updated[thread] >= self.retention:
                    self.delete_thread(thread)

    def delete_thread(self, thread_id):
        with self.memory_lock:
            super().delete_thread(thread_id)
            self.updated.pop(thread_id, None)

    def get_tuple(self, config):
        with self.memory_lock:
            self.cleanup()
            if config["configurable"]["thread_id"] not in self.storage:
                return None
            return super().get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.memory_lock:
            self.cleanup()
            rows = ([] if config and config["configurable"]["thread_id"] not in self.storage else
                    list(super().list(config, filter=filter, before=before, limit=limit)))
        yield from rows

    # MemorySaver.aput/aput_writes delegate to these synchronous methods.
    def put(self, config, checkpoint, metadata, new_versions):
        with self.memory_lock:
            result = super().put(config, clean_checkpoint(checkpoint),
                                 safe_checkpoint_value(metadata), new_versions)
            thread = config["configurable"]["thread_id"]
            ns = config["configurable"].get("checkpoint_ns", "")
            self.updated[thread] = time()
            frames = self.storage[thread][ns]
            for key in sorted(frames, reverse=True)[self.max_checkpoints:]:
                del frames[key]
            for key in list(self.writes):
                if key[:2] == (thread, ns) and key[2] not in frames:
                    del self.writes[key]
            # Blob versions are shared by snapshots; retain every referenced version.
            live = set()
            for serialized, _, _ in frames.values():
                frame = self.serde.loads_typed(serialized)
                live.update((thread, ns, channel, version) for channel, version in frame["channel_versions"].items())
            for key in list(self.blobs):
                if key[:2] == (thread, ns) and key not in live:
                    del self.blobs[key]
            return result

    def put_writes(self, config, writes, task_id, task_path=""):
        # Pregel supplies a deque, not necessarily a list/tuple.
        with self.memory_lock:
            return super().put_writes(config, clean_writes(writes), task_id, task_path)


def clean_checkpoint(checkpoint):
    result = safe_checkpoint_value(checkpoint)
    values = result.get("channel_values", {})
    if "messages" in values:
        values["messages"] = bounded_messages([], values["messages"])
    return result


def clean_writes(writes):
    return [(channel, safe_checkpoint_value(bounded_messages([], value) if channel == "messages" else value))
            for channel, value in writes]


def create_checkpointer():
    import os
    backend = os.getenv("MEMORY_BACKEND", "sqlite")
    if backend == "memory":
        return PrivacyMemorySaver()
    if backend == "sqlite":
        from .sqlite_memory import open_memory
        return open_memory()
    raise ValueError("MEMORY_BACKEND must be sqlite or memory")

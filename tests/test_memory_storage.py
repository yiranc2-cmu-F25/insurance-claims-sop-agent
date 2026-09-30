"""Real encrypted SQLite round trips; all model calls still use test doubles."""
import asyncio
import os
import sqlite3
import subprocess
import sys
from uuid import uuid4
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api import app, SESSION_COOKIE
from claim_agent.api import routes
from claim_agent.api.presenters import conversation_payload
from claim_agent.services import checkpoints, sqlite_memory, verification_session as auth
from claim_agent.services.sqlite_memory import PrivacySqliteSaver, open_memory
from claim_agent.workflow.graph import builder, graph
from claim_agent.workflow import intake, gates

MESSAGE = "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. What is the status of my denied healthcare claim from January?"


def config():
    return {"configurable": {"thread_id": uuid4().hex}}


def test_restart_restores_queue_auth_clock_and_email_receipt(tmp_path):
    key = Fernet.generate_key()
    saver = open_memory(tmp_path, key)
    flow = builder.compile(checkpointer=saver)
    session = config()
    state = flow.invoke({"user_message": MESSAGE}, config=session)
    assert state["phase"] == "POST_PROCESS"
    from claim_agent.services.email_followup import choose_email
    update = choose_email(state, state["email_offer"]["id"], "skip")
    flow.update_state(session, update, as_node="post_process")
    initial_time = state["verified_at"]
    saver.close()
    restored = open_memory(tmp_path, key)
    try:
        restarted = builder.compile(checkpointer=restored)
        state = restarted.get_state(session).values
        assert state["verified_at"] == initial_time and state["email_consent"] == "declined"
        assert state["case_memories"]["CL-2048"]["entries"]
        assert choose_email(state, state["email_offer"]["id"], "skip") == {}
        state = restarted.invoke({"user_message": "What is the appeal deadline?"}, config=session)
        assert "2026-03-18" in state["assistant_message"]
    finally:
        restored.close()


def test_fresh_process_can_read_database_without_an_api_token(tmp_path):
    saver = open_memory(tmp_path)
    session = config()
    builder.compile(checkpointer=saver).invoke({"user_message": "My name is Margaret Chen"}, config=session)
    saver.close()
    code = """
import sys
from claim_agent.services.sqlite_memory import open_memory
s = open_memory(sys.argv[1])
row = s.get_tuple({'configurable': {'thread_id': sys.argv[2]}})
assert row.checkpoint['channel_values']['collected_pii']['name'] == 'Margaret Chen'
s.close()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), session["configurable"]["thread_id"]],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "memory.key").stat().st_mode & 0o777 == 0o600


def test_expired_authentication_stays_expired_after_reopen(monkeypatch, tmp_path):
    clock = [10000.0]
    monkeypatch.setattr(auth, "time", lambda: clock[0])
    saver = open_memory(tmp_path)
    session = config()
    builder.compile(checkpointer=saver).invoke({"user_message": MESSAGE}, config=session)
    saver.close()
    clock[0] += auth.IDLE_SECONDS
    saver = open_memory(tmp_path)
    try:
        flow = builder.compile(checkpointer=saver)
        assert not conversation_payload(flow.get_state(session).values)["verified"]
        state = flow.invoke({"user_message": "What is the status?"}, config=session)
        assert state["phase"] == "VERIFY_ID" and not state["collected_pii"]
        assert not state["case_memories"]
    finally:
        saver.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sqlite_prohibited_inputs_and_ciphertext_only(monkeypatch, tmp_path, asynchronous):
    saver = open_memory(tmp_path)
    try:
        flow = builder.compile(checkpointer=saver)
        session = config()
        blocked = Mock(side_effect=AssertionError("Blocked input must not call model"))
        monkeypatch.setattr(intake, "extract_turn_with_llm", blocked)
        monkeypatch.setattr(gates, "assess_security", blocked)
        value = {"user_message": "My SSN is 123-45-6789"}
        if asynchronous:
            asyncio.run(flow.ainvoke(value, config=session))
        else:
            flow.invoke(value, config=session)
        assert all("123-45-6789" not in repr(frame) for frame in flow.get_state_history(session))
        blocked.assert_not_called()
        flow.update_state(session, {"collected_pii": {"name": "Margaret Chen"}}, as_node="pause_gate")
        with saver.cursor(transaction=False) as cur:
            for row in cur.execute("SELECT checkpoint, metadata FROM checkpoints"):
                for payload in row:
                    assert b"Margaret Chen" not in payload and b"123-45-6789" not in payload
                    assert payload.startswith(b"gAAAA")
            for row in cur.execute("SELECT value FROM writes"):
                assert row[0].startswith(b"gAAAA")
    finally:
        saver.close()


def test_wrong_or_missing_key_and_second_worker_fail_without_resetting_data(tmp_path):
    key = Fernet.generate_key()
    saver = open_memory(tmp_path, key)
    session = config()
    builder.compile(checkpointer=saver).invoke({"user_message": "My name is Margaret Chen"}, config=session)
    with pytest.raises(RuntimeError, match="one application worker"):
        open_memory(tmp_path, key)
    saver.close()
    with pytest.raises(RuntimeError, match="key"):
        open_memory(tmp_path, Fernet.generate_key())
    with pytest.raises(RuntimeError, match="key is missing"):
        open_memory(tmp_path)
    saver = open_memory(tmp_path, key)
    assert saver.get_tuple(session).checkpoint["channel_values"]["collected_pii"]["name"] == "Margaret Chen"
    saver.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_checkpoint_bound_preserves_shared_values_and_cleans_pending_writes(monkeypatch, tmp_path, backend):
    if backend == "memory":
        saver = checkpoints.PrivacyMemorySaver(max_checkpoints=3)
    else:
        saver = PrivacySqliteSaver(sqlite3.connect(str(tmp_path / "test.sqlite"), check_same_thread=False), Fernet.generate_key(), max_checkpoints=3)
    try:
        flow = builder.compile(checkpointer=saver)
        session = config()
        flow.invoke({"user_message": "My name is Margaret Chen"}, config=session)
        for i in range(10):
            flow.update_state(session, {"assistant_message": "note " + str(i)}, as_node="pause_gate")
        assert len(list(flow.get_state_history(session))) == 3
        assert flow.get_state(session).values["collected_pii"]["name"] == "Margaret Chen"
        for frame in flow.get_state_history(session):
            assert frame.values["collected_pii"]["name"] == "Margaret Chen"
        if backend == "sqlite":
            with saver.cursor(transaction=False) as cur:
                assert cur.execute("SELECT COUNT(*) FROM writes w LEFT JOIN checkpoints c USING(thread_id, checkpoint_ns, checkpoint_id) WHERE c.checkpoint_id IS NULL").fetchone()[0] == 0
    finally:
        if backend == "sqlite":
            saver.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_expired_session_deleted_without_read_refresh(monkeypatch, tmp_path, backend):
    clock = [10000.0]
    monkeypatch.setattr(checkpoints if backend == "memory" else sqlite_memory, "time", lambda: clock[0])
    if backend == "memory":
        saver = checkpoints.PrivacyMemorySaver(retention=100)
    else:
        saver = PrivacySqliteSaver(sqlite3.connect(str(tmp_path / "test.sqlite"), check_same_thread=False), Fernet.generate_key(), retention=100)
    try:
        flow = builder.compile(checkpointer=saver)
        session = config()
        flow.invoke({"user_message": "My name is Margaret Chen"}, config=session)
        clock[0] += 99
        assert flow.get_state(session).values
        clock[0] += 1
        assert not flow.get_state(session).values
        assert not list(flow.get_state_history(session))
        if backend == "sqlite":
            with saver.cursor(transaction=False) as cur:
                for table in ("checkpoints", "writes", "memory_entries"):
                    assert cur.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        else:
            assert not saver.storage and not saver.writes and not saver.blobs
    finally:
        if backend == "sqlite":
            saver.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_forget_deletes_only_current_session_and_rotates_cookie(monkeypatch, tmp_path, persistent):
    saver = open_memory(tmp_path) if persistent else checkpoints.PrivacyMemorySaver()
    flow = builder.compile(checkpointer=saver)
    monkeypatch.setattr(routes, "graph", flow)
    try:
        with TestClient(app) as client, TestClient(app) as other:
            before = client.post("/api/chat", json={"message": MESSAGE}).json()
            old_cookie = client.cookies[SESSION_COOKIE]
            other.post("/api/chat", json={"message": "My name is Margaret Chen"})
            other_config = {"configurable": {"thread_id": other.cookies[SESSION_COOKIE]}}
            deleted = client.delete("/api/conversation")
            assert deleted.status_code == 200 and not deleted.json()["verified"]
            assert client.cookies[SESSION_COOKIE] != old_cookie
            assert not flow.get_state({"configurable": {"thread_id": old_cookie}}).values
            assert not list(flow.get_state_history({"configurable": {"thread_id": old_cookie}}))
            assert flow.get_state(other_config).values["collected_pii"]
            assert client.post("/api/email-choice", json={"offer_id": before["email_offer"]["id"], "choice": "send"}).status_code == 409
    finally:
        if persistent:
            saver.close()

"""Prohibited inputs must never enter checkpoint history, including pending writes."""
import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import SESSION_COOKIE, app
from claim_agent.workflow import intake, gates
from claim_agent.workflow.graph import graph


@pytest.mark.parametrize("message,secret", [
    ("My SSN is 123-45-6789", "123-45-6789"),
    ("My key is sk-testabcdefghijklmnop", "sk-testabcdefghijklmnop"),
    ("Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"),
    ("My SSN is １２３－４５－６７８９", "１２３－４５－６７８９"),
])
@pytest.mark.parametrize("entry", ["http", "graph", "async_graph"])
def test_sensitive_input_absent_from_all_persisted_history(monkeypatch, message, secret, entry):
    model = Mock(side_effect=AssertionError("Prohibited input must not reach any model"))
    monkeypatch.setattr(intake, "extract_turn_with_llm", model)
    monkeypatch.setattr(gates, "assess_security", model)
    config = {"configurable": {"thread_id": uuid4().hex}}
    if entry == "http":
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": message})
            assert response.status_code == 200
            config["configurable"]["thread_id"] = client.cookies[SESSION_COOKIE]
            assert "Please do not send" in response.json()["reply"]
    elif entry == "graph":
        graph.invoke({"user_message": message}, config=config)
    else:
        asyncio.run(graph.ainvoke({"user_message": message}, config=config))
    model.assert_not_called()
    history = list(graph.get_state_history(config))
    assert history
    for frame in history:
        assert secret not in repr(frame)
    saver = graph.checkpointer
    thread = config["configurable"]["thread_id"]
    for storage in (saver.storage, saver.writes, saver.blobs):
        for key, value in storage.items():
            if key == thread or (isinstance(key, tuple) and key[0] == thread):
                assert secret not in repr(value)
                assert secret.encode() not in repr(value).encode()
    assert not graph.get_state(config).values.get("collected_pii")


def test_valid_input_after_block_is_processed():
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": "My SSN is 123-45-6789"})
        data = client.post("/api/chat", json={"message": "My name is Margaret Chen"}).json()
        assert "2 more" in data["reply"]
        assert data["phase"] == "VERIFY_ID"

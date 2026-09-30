from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import SESSION_COOKIE, app
from claim_agent.services import verification_session as auth, email_followup
from claim_agent.guardrails.access import has_access
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph
from claim_agent.llm.schemas import TurnExtraction

OWNER = dict(name="Margaret Chen", dob="1985-03-15", id_last4="4472")
MESSAGE = "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. What is the status of my denied healthcare claim from January?"


@pytest.fixture
def clock(monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(auth, "time", lambda: now[0])
    return now


def test_warning_countdown_reads_do_not_extend_idle_and_expiry_survives_refresh(clock):
    with TestClient(app) as client:
        initial = client.post("/api/chat", json={"message": MESSAGE}).json()
        assert initial["verified"] and initial["verification"]["remaining_seconds"] == auth.IDLE_SECONDS
        clock[0] += auth.IDLE_SECONDS - 119
        near = client.get("/api/conversation").json()
        assert near["verification"]["remaining_seconds"] == 119
        assert near["verification"]["warning_seconds"] == 120
        clock[0] += 119
        for _ in range(2):
            expired = client.get("/api/conversation").json()
            assert expired["phase"] == "VERIFY_ID" and not expired["verified"]
            assert expired["verification"]["expired"]
            assert not expired["claim_id"] and not expired["email_offer"]["can_send"]
            assert "expired" in expired["reply"]
        config = {"configurable": {"thread_id": client.cookies[SESSION_COOKIE]}}
        state = graph.get_state(config).values
        assert not state["collected_pii"] and not state["discussed_answers"]
        partial = client.post("/api/chat", json={"message": "My name is Margaret Chen"}).json()
        assert not partial["verified"] and "2 more" in partial["reply"]
        fresh = client.post("/api/chat", json={"message": "DOB is 1985-03-15, SSN last four is 4472"}).json()
        assert fresh["verified"] and not fresh["verification"]["expired"]


@pytest.mark.parametrize("path", ["/api/email-choice", "/api/email-status"])
def test_email_buttons_cannot_bypass_expiry(monkeypatch, clock, path):
    send = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", send)
    with TestClient(app) as client:
        data = client.post("/api/chat", json={"message": MESSAGE}).json()
        clock[0] += auth.IDLE_SECONDS
        body = {"offer_id": data["email_offer"]["id"]}
        if path.endswith("choice"):
            body["choice"] = "send"
        assert client.post(path, json=body).status_code == 409
        assert not client.get("/api/conversation").json()["verified"]
        send.assert_not_called()


def test_chat_activity_extends_idle_but_never_absolute_age(clock):
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": MESSAGE})
        for _ in range(4):
            clock[0] += 800
            data = client.post("/api/chat", json={"message": "Thank you"}).json()
            assert data["verified"]
        assert data["verification"]["remaining_seconds"] == 400
        clock[0] += 400
        expired = client.post("/api/chat", json={"message": "What is the status?"}).json()
        assert not expired["verified"] and expired["phase"] == "VERIFY_ID"


def test_human_pause_and_resume_do_not_preserve_expired_access(clock):
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": MESSAGE})
        client.post("/api/chat", json={"message": "Please talk to a human representative"})
        assert client.post("/api/handoff").json()["handoff"]["summary"]["verification"] == "verified"
        clock[0] += auth.IDLE_SECONDS
        data = client.get("/api/conversation").json()
        assert data["handoff"]["status"] == "requested" and data["handoff"]["summary"] is None
        resumed = client.post("/api/handoff/resume").json()
        assert resumed["phase"] == "VERIFY_ID" and not resumed["verified"]


def test_failure_budget_complete_mismatches_only_and_cooldown(monkeypatch, clock):
    config = {"configurable": {"thread_id": uuid4().hex}}
    def turn(**fields):
        monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", **fields))
        return graph.invoke({"user_message": "Identity response"}, config=config)
    for _ in range(6):
        state = turn(name="Wrong Person")
    assert state.get("verification_failed_attempts", 0) == 0
    wrong = dict(name="Wrong Person", dob="1980-01-01", id_last4="0000")
    for count in range(1, auth.MAX_FAILURES + 1):
        state = turn(**wrong)
        assert state["verification_failed_attempts"] == count
        if count < auth.MAX_FAILURES:
            assert turn()["verification_failed_attempts"] == count
    assert auth.verification_is_locked(state) and state["handoff_reason"] == "verification_locked"
    blocked = Mock(side_effect=AssertionError("No LLM during cooldown"))
    monkeypatch.setattr(intake, "extract_turn_with_llm", blocked)
    graph.invoke({"user_message": MESSAGE}, config=config)
    blocked.assert_not_called()
    clock[0] += auth.LOCK_SECONDS
    state = turn(**OWNER)
    assert state["verified_party_id"] == "P9" and state["verification_failed_attempts"] == 0
    assert not auth.verification_is_locked(state)


def test_missing_or_expired_server_timestamps_fail_closed(clock):
    base = {"verified_party_id": "P9", "verification_matches": ["name", "dob", "id_last4"]}
    assert not has_access(base)
    assert not has_access({**base, "verified_at": 10000, "verified_last_active_at": 10001})
    assert has_access({**base, "verified_at": 10000, "verified_last_active_at": 10000})
    clock[0] += auth.IDLE_SECONDS
    assert not has_access({**base, "verified_at": 10000, "verified_last_active_at": 10000})

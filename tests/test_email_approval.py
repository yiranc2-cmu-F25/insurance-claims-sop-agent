from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import app, SESSION_COOKIE
from claim_agent.services import email_followup as email
from claim_agent.workflow.graph import graph
from claim_agent.tools.email import get_consent_status


MESSAGE = "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. What is the status of my denied healthcare claim from January?"


@pytest.fixture
def pending(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(email, "time", lambda: clock[0])
    sender = Mock(wraps=email.send_email_summary)
    monkeypatch.setattr(email, "send_email_summary", sender)
    with TestClient(app) as client:
        state = client.post("/api/chat", json={"message": MESSAGE}).json()
        offer = state["email_offer"]["id"]
        state = client.post("/api/email-choice", json={"offer_id": offer, "choice": "send"}).json()
        assert state["email_offer"]["delivery_status"] == "pending"
        assert not state["email_offer"]["pending"] and state["email_delivery"]["can_poll"]
        assert "pending delivery approval" in state["reply"]
        yield client, offer, clock, sender


def poll(client, offer):
    return client.post("/api/email-status", json={"offer_id": offer})


def test_initial_poll_is_pending_and_unknown_scenario_fails_closed():
    assert get_consent_status() == "pending"
    assert get_consent_status("default", 1) == "approved"
    assert get_consent_status("not-a-scenario") == "unknown"


def test_pending_approval_advances_without_resending(pending):
    client, offer, clock, sender = pending
    assert poll(client, offer).json()["email_delivery"]["status"] == "pending"
    clock[0] += 2
    state = poll(client, offer).json()
    assert state["email_delivery"]["status"] == "approved"
    assert state["email_offer"]["delivery_status"] == "approved"
    assert not state["email_delivery"]["can_poll"]
    assert "No real email was sent" in state["reply"]
    assert poll(client, offer).json()["email_delivery"] == state["email_delivery"]
    sender.assert_called_once()
    assert client.get("/api/conversation").json()["email_delivery"] == state["email_delivery"]


def test_timeout_scenario_is_bounded_without_resending(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL_SCENARIO", "timeout")
    clock = [1000.0]
    monkeypatch.setattr(email, "time", lambda: clock[0])
    sender = Mock(wraps=email.send_email_summary)
    monkeypatch.setattr(email, "send_email_summary", sender)
    with TestClient(app) as client:
        state = client.post("/api/chat", json={"message": MESSAGE}).json()
        offer = state["email_offer"]["id"]
        client.post("/api/email-choice", json={"offer_id": offer, "choice": "send"})
        for _ in range(4):
            clock[0] += 2
            state = poll(client, offer).json()
        assert state["email_delivery"]["status"] == "timeout"
        assert state["handoff"]["reason"] == "email_failure"
        assert "timed out" in state["reply"]
        sender.assert_called_once()


def test_expired_wall_clock_times_out_even_without_polling(pending):
    client, offer, clock, sender = pending
    clock[0] += 31
    state = poll(client, offer).json()
    assert state["email_delivery"]["status"] == "timeout"
    assert not state["email_delivery"]["can_poll"]
    sender.assert_called_once()


def test_failed_poll_does_not_leak_error_or_resend(monkeypatch, pending):
    client, offer, clock, sender = pending
    monkeypatch.setattr(email, "get_consent_status", Mock(side_effect=RuntimeError("secret-token")))
    clock[0] += 2
    response = poll(client, offer)
    assert response.json()["email_delivery"]["status"] == "failed"
    assert "secret-token" not in response.text
    sender.assert_called_once()


def test_later_question_preserves_pending_job_and_new_offer(pending):
    client, old_offer, clock, sender = pending
    newer = client.post("/api/chat", json={"message": "What is the appeal deadline?"}).json()
    new_offer = newer["email_offer"]["id"]
    assert new_offer != old_offer and not newer["email_offer"]["can_send"]
    assert newer["email_offer"]["can_skip"]
    assert client.post("/api/email-choice", json={"offer_id": new_offer, "choice": "send"}).status_code == 409
    clock[0] += 2
    state = poll(client, old_offer).json()
    assert state["email_delivery"]["status"] == "approved"
    assert state["email_offer"]["id"] == new_offer and state["email_offer"]["can_send"]
    assert state["email_consent"] is None and state["reply"] == newer["reply"]
    sender.assert_called_once()


def test_poll_is_session_bound_and_client_cannot_supply_scenario(pending):
    client, offer, _, _ = pending
    with TestClient(app) as other:
        assert poll(other, offer).status_code == 409
    assert client.post("/api/email-status", json={"offer_id": offer, "scenario": "default"}).status_code == 422
    data = client.get("/api/conversation").json()["email_delivery"]
    assert not {"party_id", "claim_id", "caller_id", "recipient", "scenario", "summary"} & data.keys()


def test_pause_blocks_approval_checks_and_resume_restores_them(pending):
    client, offer, clock, _ = pending
    client.post("/api/chat", json={"message": "Talk to a human representative."})
    client.post("/api/handoff")
    clock[0] += 2
    assert poll(client, offer).status_code == 409
    assert not client.get("/api/conversation").json()["email_delivery"]["can_poll"]
    client.post("/api/handoff/resume")
    assert poll(client, offer).json()["email_delivery"]["status"] == "approved"


@pytest.mark.parametrize("update", [
    {"verified_party_id": "P12"}, {"verification_matches": ["name"]},
    {"security_risk": "high"}, {"pii_conflicts": {"name": "conflict"}},
])
def test_poll_rechecks_identity_and_safety(pending, update):
    client, offer, _, _ = pending
    config = {"configurable": {"thread_id": client.cookies[SESSION_COOKIE]}}
    graph.update_state(config, update, as_node="pause_gate")
    assert poll(client, offer).status_code == 409

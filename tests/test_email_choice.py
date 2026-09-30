from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import SESSION_COOKIE, app
from claim_agent.services import email_followup
from claim_agent.llm import client as llm
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


VERIFIED_MESSAGE = (
    "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
    "What is the status of my denied healthcare claim from January?"
)


@pytest.fixture
def offered_client():
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": VERIFIED_MESSAGE})
        assert response.status_code == 200
        offer = response.json()["email_offer"]
        assert offer["pending"] and offer["can_send"] and offer["can_skip"]
        yield client, offer["id"]


def choose(client, offer_id, choice="send"):
    return client.post("/api/email-choice", json={"offer_id": offer_id, "choice": choice})


def test_send_button_uses_saved_summary_no_model_and_no_duplicate(monkeypatch, offered_client):
    client, offer_id = offered_client
    email = Mock(return_value={"status": "approved"})
    extractor = Mock(side_effect=AssertionError("Button must not invoke the LLM"))
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    response = choose(client, offer_id)
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "POST_PROCESS" and data["email_consent"] == "accepted"
    assert not data["email_offer"]["pending"]
    assert data["email_offer"]["delivery_status"] == "approved"
    assert "No real email was sent" in data["reply"]
    repeated = choose(client, offer_id).json()
    # The decision receipt is idempotent; the live authentication countdown is not.
    assert repeated.pop("verification")["remaining_seconds"] <= data["verification"]["remaining_seconds"]
    assert repeated == {key: value for key, value in data.items() if key != "verification"}
    assert choose(client, offer_id, "skip").status_code == 409
    assert client.get("/api/conversation").json()["email_offer"] == data["email_offer"]
    email.assert_called_once()
    assert email.call_args.args[:2] == ("P9", "CL-2048")
    assert "Claim CL-2048" in email.call_args.args[2]
    assert VERIFIED_MESSAGE not in email.call_args.args[2]
    assert email.call_args.kwargs == {"consent": True}
    extractor.assert_not_called()


def test_skip_button_never_calls_email_and_cannot_be_reversed(monkeypatch, offered_client):
    client, offer_id = offered_client
    email = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    response = choose(client, offer_id, "skip")
    assert response.status_code == 200
    assert response.json()["email_consent"] == "declined"
    assert response.json()["email_offer"]["delivery_status"] == "not_sent"
    assert not response.json()["email_offer"]["pending"]
    assert choose(client, offer_id, "skip").status_code == 200
    assert choose(client, offer_id, "send").status_code == 409
    email.assert_not_called()
    followup = client.post("/api/chat", json={"message": "What is the appeal deadline?"}).json()
    assert followup["email_offer"]["pending"] and followup["email_offer"]["id"] != offer_id


def test_old_offer_cannot_send_after_new_question(monkeypatch, offered_client):
    client, old_id = offered_client
    email = Mock(return_value={"status": "approved"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    next_turn = client.post("/api/chat", json={"message": "What is the appeal deadline?"}).json()
    new_id = next_turn["email_offer"]["id"]
    assert new_id != old_id
    assert choose(client, old_id).status_code == 409
    email.assert_not_called()
    assert choose(client, new_id).status_code == 200
    assert "2026-03-18" in email.call_args.args[2]


def test_refresh_restores_current_offer_without_private_payload(offered_client):
    client, offer_id = offered_client
    data = client.get("/api/conversation").json()
    assert data["email_offer"]["id"] == offer_id and data["email_offer"]["pending"]
    for field in ("party_id", "claim_id", "summary", "recipient"):
        assert field not in data["email_offer"]


def test_offer_required_and_bound_to_session(offered_client):
    _, offer_id = offered_client
    with TestClient(app) as other:
        assert choose(other, offer_id).status_code == 409
        own = other.post("/api/chat", json={"message": VERIFIED_MESSAGE}).json()["email_offer"]
        assert own["id"] != offer_id
        assert choose(other, offer_id).status_code == 409


@pytest.mark.parametrize("extra", [
    {"choice": "yes"}, {"offer_id": "invalid"}, {"recipient": "other@example.com"},
    {"summary": "Invented summary"}, {"claim_id": "CL-9999"},
])
def test_endpoint_rejects_invalid_or_injected_arguments(monkeypatch, offered_client, extra):
    client, offer_id = offered_client
    email = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    response = client.post("/api/email-choice", json={"offer_id": offer_id, "choice": "send", **extra})
    assert response.status_code == 422
    email.assert_not_called()


@pytest.mark.parametrize("update", [
    {"phase": "VERIFY_ID"}, {"verified_party_id": None},
    {"verification_matches": ["name", "dob"]}, {"selected_claim_id": "CL-9999"},
    {"security_risk": "high"}, {"authorization_denied": True},
    {"pii_conflicts": {"name": "conflicting"}}, {"pii_errors": {"dob": "invalid"}},
])
def test_send_enforces_server_side_gates(monkeypatch, offered_client, update):
    client, offer_id = offered_client
    email = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    config = {"configurable": {"thread_id": client.cookies[SESSION_COOKIE]}}
    graph.update_state(config, update, as_node="pause_gate")
    assert choose(client, offer_id).status_code == 409
    assert not client.get("/api/conversation").json()["email_offer"]["can_send"]
    email.assert_not_called()


def test_human_pause_blocks_buttons_and_resume_restores_offer(monkeypatch, offered_client):
    client, offer_id = offered_client
    email = Mock(return_value={"status": "approved"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    client.post("/api/chat", json={"message": "Talk to a human representative."})
    paused = client.post("/api/handoff").json()
    assert not paused["email_offer"]["can_send"] and not paused["email_offer"]["can_skip"]
    assert choose(client, offer_id).status_code == 409
    assert choose(client, offer_id, "skip").status_code == 409
    email.assert_not_called()
    resumed = client.post("/api/handoff/resume").json()
    assert resumed["email_offer"]["id"] == offer_id and resumed["email_offer"]["can_send"]
    assert choose(client, offer_id).status_code == 200


def test_existing_summary_button_does_not_require_live_model(monkeypatch, offered_client):
    client, offer_id = offered_client
    extractor = Mock(side_effect=llm.LLMUnavailable())
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    email = Mock(return_value={"status": "approved"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    assert client.post("/api/chat", json={"message": "yes"}).status_code == 503
    extractor.reset_mock()
    assert choose(client, offer_id).status_code == 200
    extractor.assert_not_called()
    email.assert_called_once()


@pytest.mark.parametrize("delivery", [{"status": "pending"}, {"status": "failed"}, RuntimeError("secret")])
def test_pending_or_failed_delivery_is_not_claimed_as_sent(monkeypatch, offered_client, delivery):
    client, offer_id = offered_client
    email = Mock(side_effect=delivery) if isinstance(delivery, Exception) else Mock(return_value=delivery)
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    response = choose(client, offer_id)
    assert response.status_code == 200
    data = response.json()
    assert "secret" not in data["reply"] and not data["email_offer"]["pending"]
    if delivery == {"status": "pending"}:
        assert "pending delivery approval" in data["reply"]
    else:
        assert data["handoff"]["reason"] == "email_failure"
    assert choose(client, offer_id).status_code == 200
    email.assert_called_once()


def test_concurrent_button_clicks_only_call_tool_once(monkeypatch, offered_client):
    client, offer_id = offered_client
    cookie = client.cookies[SESSION_COOKIE]
    email = Mock(return_value={"status": "approved"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    def click(_):
        with TestClient(app) as tab:
            tab.cookies.set(SESSION_COOKIE, cookie)
            response = choose(tab, offer_id)
            assert response.status_code == 200
            return response.json()["email_offer"]["delivery_status"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(click, range(4))) == ["approved"] * 4
    email.assert_called_once()


def test_summary_has_status_outcome_discussion_and_next_steps(monkeypatch, offered_client):
    client, offer_id = offered_client
    from datetime import date
    monkeypatch.setattr("claim_agent.tools.claims.current_date", lambda: date(2026, 9, 30))
    email = Mock(return_value={"status": "approved", "recipient": "margaret@email.com"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    offer = client.get("/api/conversation").json()["email_offer"]
    assert offer["recipient_masked"] == "m***@email.com"
    assert offer["preview"].startswith("Claim CL-2048 (healthcare)") and "status: denied" in offer["preview"]
    data = choose(client, offer_id).json()
    summary = email.call_args.args[2]
    assert summary == offer["preview"]
    for part in ("Outcome: Healthcare claim denied", "What we discussed:", "Follow-up items / next steps:",
                 "Documents still needed: pathology report, office note", "Appeal deadline on file: 2026-03-18"):
        assert part in summary
    assert "already passed" in summary
    assert "m***@email.com" in data["reply"] and "No real email was sent" in data["reply"]
    assert data["email_offer"]["preview"] is None and data["email_offer"]["recipient_masked"] is None

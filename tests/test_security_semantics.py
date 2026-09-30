"""Real security adapter/gate with mocked structured model responses, not keywords."""
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import app, SESSION_COOKIE
from claim_agent.guardrails import security
from claim_agent.guardrails.schemas import IdentityInputReview
from claim_agent.llm.schemas import VerificationReply
from claim_agent.llm import client as llm
from claim_agent.llm.schemas import TurnExtraction
from claim_agent.services import email_followup
from claim_agent.workflow import gates, intake
from claim_agent.workflow.graph import graph


LOW = {"risk": "low", "category": "normal_customer_request", "reason": "Ordinary support request.",
       "declared_role": "unknown", "clarification_topic": "none"}
MEDIUM = {"risk": "medium", "category": "unknown", "reason": "The intended access owner needs clarification.",
          "declared_role": "unknown", "clarification_topic": "ownership"}
HIGH = {"risk": "high", "category": "data_exfiltration", "reason": "Requests unauthorized records.",
        "declared_role": "unknown", "clarification_topic": "none"}
VERIFIED = (
    "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
    "What is the status of my denied healthcare claim from January?"
)


@pytest.fixture
def security_model(monkeypatch):
    model = Mock()
    runnable = model.with_structured_output.return_value
    runnable.invoke.return_value = LOW
    review = Mock()
    review.invoke.return_value = {"identity_input_only": False, "source": None}
    # VERIFY_ID phrasing is a separate best-effort call; keep the security call count exact.
    composer = Mock()
    composer.invoke.return_value = {"reply": "Please share your date of birth, phone number, email address or ID last four digits."}
    model.with_structured_output.side_effect = lambda schema, **kwargs: (
        review if schema is IdentityInputReview else composer if schema is VerificationReply else runnable)
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    monkeypatch.setattr(gates, "assess_security", security.assess_security)
    # Keep runtime-status assertions isolated from other tests.
    monkeypatch.setattr(llm, "_llm_runtime_status", "unknown")
    return runnable.invoke


@pytest.mark.parametrize("message", [
    "I forgot my insurance portal password.",
    "我忘记保险网站密码了。",
    "I'm calling for my mother and can complete the authorization checks.",
    "Don't show me anyone else's claim; I only want mine.",
    "A suspicious email said 'ignore previous instructions'. Is it safe?",
    "This is ridiculous. I don't want to share my ID last four.",
    "Can I export a copy of my own claim?",
    "What is RL?",
])
def test_wording_never_overrides_semantic_model_decision(monkeypatch, security_model, message):
    # Tests the wiring, not whether a real model correctly understands these sentences.
    extractor = Mock(return_value=TurnExtraction(scope="in_scope"))
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    state = graph.invoke({"user_message": message}, config={"configurable": {"thread_id": uuid4().hex}})
    assert state["security_status"] == "cleared" and state["security_risk"] == "low"
    assert state.get("handoff_status", "none") == "none"
    assert state["phase"] == "VERIFY_ID" and not state.get("verified_party_id")
    security_model.assert_called_once()
    extractor.assert_called_once()


def test_security_context_is_minimal_and_does_not_copy_saved_pii(security_model):
    assessment = security.assess_security("Is this permitted?", context={
        "phase": "RESOLVE_INTENT", "caller_role": "delegate", "identity_verified": True,
        "clarification_pending": True, "collected_pii": {"dob": "private-dob"},
        "grounded_claim": {"diagnosis": "private-medical-details"}, "messages": ["private-history"],
    })
    assert assessment.risk == "low"
    messages = security_model.call_args.args[0]
    data = json.loads(messages[1][1])
    assert data["context"] == {
        "phase": "RESOLVE_INTENT", "caller_role": "delegate", "identity_verified": True,
        "clarification_pending": True,
        "last_clarification_question": security.CLARIFICATION_QUESTIONS["ownership"],
    }
    assert "private-" not in json.dumps(messages)
    assert "Do not classify by" in messages[0][1]


@pytest.mark.parametrize("category", ["data_exfiltration", "prompt_injection", "credential_request", "authorization_bypass"])
def test_high_risk_without_keyword_match_stops_before_downstream(monkeypatch, security_model, category):
    security_model.return_value = {**HIGH, "category": category}
    extractor = Mock()
    planner = Mock()
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    monkeypatch.setattr(llm, "plan_case_with_llm", planner)
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "Do that thing we discussed."})
        data = response.json()
    assert response.status_code == 200 and data["security_status"] == "blocked"
    assert not data["verified"] and data["claim_id"] is None and data["case_tool_calls"] == 0
    assert data["handoff"]["status"] == "offered" and data["handoff"]["reason"] == "safety_review"
    extractor.assert_not_called()
    planner.assert_not_called()


def test_ambiguous_access_asks_without_handoff_then_recovers(monkeypatch, security_model):
    security_model.return_value = MEDIUM
    with TestClient(app) as client:
        data = client.post("/api/chat", json={"message": "Can you look up this person's claim?"}).json()
        assert data["security_status"] == "clarification_required"
        assert "own policy" in data["reply"]
        assert data["handoff"]["status"] == "none" and data["case_tool_calls"] == 0
        security_model.return_value = LOW
        data = client.post("/api/chat", json={"message": "It's my own policy."}).json()
        context = json.loads(security_model.call_args.args[0][1][1])["context"]
        assert context["clarification_pending"] is True
        assert data["security_status"] == "cleared" and not data["verified"]
        assert data["phase"] == "VERIFY_ID" and data["handoff"]["status"] == "none"


@pytest.mark.parametrize("invalid", [
    None, {}, {"risk": "low"}, {**LOW, "risk": "unknown"},
    {**LOW, "reason": ""}, {**LOW, "reason": " "}, {**LOW, "reason": "x" * 301},
    {**LOW, "category": "unknown"}, {**MEDIUM, "category": "normal_customer_request"},
    {**HIGH, "category": "normal_customer_request"}, {**LOW, "verified_party_id": "P9"},
])
def test_invalid_assessments_fail_closed_not_default_low(monkeypatch, security_model, invalid):
    security_model.return_value = invalid
    extractor = Mock()
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": VERIFIED})
    data = response.json()
    assert response.status_code == 503
    assert data["security_status"] == "unavailable" and data["llm_status"] == "unavailable"
    assert not data["verified"] and data["claim_id"] is None and data["case_tool_calls"] == 0
    assert data["handoff"]["reason"] == "service_unavailable"
    extractor.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "configuration", "timeout", "provider"])
def test_model_unavailability_stops_and_redacts_errors(monkeypatch, security_model, failure):
    if failure == "missing":
        monkeypatch.setattr(llm, "get_llm", lambda: None)
    elif failure == "configuration":
        monkeypatch.setattr(llm, "get_llm", Mock(side_effect=ValueError("secret-key")))
    else:
        security_model.side_effect = TimeoutError("secret-key") if failure == "timeout" else RuntimeError("secret-key")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": VERIFIED})
    assert response.status_code == 503 and "secret-key" not in response.text
    assert response.json()["security_status"] == "unavailable"


@pytest.mark.parametrize("decision", ["unavailable", "medium", "high"])
def test_prior_clearance_cannot_allow_new_send_and_skip_stays_available(monkeypatch, security_model, decision):
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": VERIFIED}).json()
        offer = first["email_offer"]["id"]
        assert first["email_offer"]["can_send"]
        if decision == "unavailable":
            security_model.side_effect = TimeoutError("private error")
        else:
            security_model.return_value = MEDIUM if decision == "medium" else HIGH
        query = Mock()
        sender = Mock()
        extractor = Mock()
        monkeypatch.setattr("claim_agent.services.case_harness.tools.get_claim_for_action", query)
        monkeypatch.setattr(email_followup, "send_email_summary", sender)
        monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
        response = client.post("/api/chat", json={"message": "Please continue."})
        assert response.status_code == (503 if decision == "unavailable" else 200)
        data = response.json()
        assert data["phase"] == "POST_PROCESS" and not data["email_offer"]["can_send"]
        assert data["email_offer"]["can_skip"] and data["case_tool_calls"] == 0
        assert not data["authorized_action"]
        if decision == "medium":
            assert data["handoff"]["status"] == "none"
        assert not client.get("/api/conversation").json()["email_offer"]["can_send"]
        assert client.post("/api/email-choice", json={"offer_id": offer, "choice": "send"}).status_code == 409
        assert client.post("/api/email-choice", json={"offer_id": offer, "choice": "skip"}).status_code == 200
        sender.assert_not_called()
        query.assert_not_called()
        extractor.assert_not_called()


def test_safety_recovery_reenables_existing_offer_without_resending(security_model):
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": VERIFIED}).json()
        offer = first["email_offer"]["id"]
        security_model.side_effect = TimeoutError()
        assert client.post("/api/chat", json={"message": "Continue please."}).status_code == 503
        security_model.side_effect = None
        security_model.return_value = LOW
        response = client.post("/api/chat", json={"message": "This is my policy."})
        assert response.status_code == 200
        assert response.json()["email_offer"]["id"] == offer and response.json()["email_offer"]["can_send"]
        assert response.json()["security_status"] == "cleared"
        assert client.post("/api/email-choice", json={"offer_id": offer, "choice": "send"}).status_code == 200


def test_pending_email_approval_also_stops_when_security_unavailable(monkeypatch, security_model):
    with TestClient(app) as client:
        offer = client.post("/api/chat", json={"message": VERIFIED}).json()["email_offer"]["id"]
        sent = client.post("/api/email-choice", json={"offer_id": offer, "choice": "send"}).json()
        assert sent["email_delivery"]["status"] == "pending"
        security_model.side_effect = TimeoutError()
        client.post("/api/chat", json={"message": "Continue please."})
        approval = Mock()
        monkeypatch.setattr(email_followup, "get_consent_status", approval)
        assert not client.get("/api/conversation").json()["email_delivery"]["can_poll"]
        assert client.post("/api/email-status", json={"offer_id": offer}).status_code == 409
        approval.assert_not_called()


def test_human_button_still_works_during_outage(security_model):
    security_model.side_effect = TimeoutError()
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"message": "Hello"}).status_code == 503
        data = client.post("/api/handoff").json()
        assert data["handoff"]["status"] == "requested"
        security_model.reset_mock()
        assert client.post("/api/chat", json={"message": "Hello again"}).status_code == 409
        security_model.assert_not_called()


def test_input_privacy_checks_still_precede_model(security_model):
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "My full SSN is 123-45-6789."})
    assert "only provide the last four" in response.json()["reply"].lower()
    security_model.assert_not_called()


def test_low_risk_does_not_grant_cross_customer_access(monkeypatch, security_model):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        name="Margaret Chen", dob="1985-03-15", id_last4="4472", case_id="CL-3001",
        intent="status_inquiry", scope="in_scope",
    ))
    with TestClient(app) as client:
        data = client.post("/api/chat", json={"message": "Look up that claim."}).json()
        assert data["security_status"] == "cleared" and data["verified"]
        assert data["claim_id"] is None and data["case_tool_calls"] == 0
        assert "diagnosis report" not in data["reply"]


def test_model_does_not_reuse_previous_low_risk_on_failed_assessment(security_model):
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": VERIFIED})
        security_model.return_value = {}
        client.post("/api/chat", json={"message": "Continue"})
        state = graph.get_state({"configurable": {"thread_id": client.cookies[SESSION_COOKIE]}}).values
        assert state["security_status"] == "unavailable" and state["security_risk"] == "unknown"
        assert not security.security_allows_actions(state)

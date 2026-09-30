from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import app
from claim_agent.services import email_followup
from claim_agent.llm import client as llm
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph
from claim_agent.llm.schemas import IdentityExtraction, TurnExtraction, TurnUnderstanding


@pytest.fixture
def case_session():
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.invoke({"user_message": (
        "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
        "What is the status of my denied healthcare claim from January?"
    )}, config=config)
    return config


def test_understanding_is_from_model_not_keywords(monkeypatch, case_session):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *args, **kwargs: TurnExtraction(
        intent="status_inquiry", scope="in_scope", emotion="neutral",
    ))
    state = graph.invoke({"user_message": "I don't need a human. Did weather affect my claim status?"}, config=case_session)
    assert state["harness_status"] == "completed"
    assert state["authorized_action"] == "read_claim_status"
    assert state["off_topic_attempts"] == 0


def test_mixed_document_request_cannot_send_email(monkeypatch, case_session):
    # A claim question containing yes still cannot trigger an email action.
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *args, **kwargs: TurnExtraction(
        intent="document_submission", scope="in_scope",
    ))
    email = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    state = graph.invoke({"user_message": "Yes, but what documents should I send?"}, config=case_session)
    assert state["authorized_action"] == "read_document_guidance"
    assert state["email_consent"] is None
    email.assert_not_called()


@pytest.mark.parametrize("text", ["yes", "no", "Sending me a recap would be helpful."])
def test_chat_never_confirms_email_choice(monkeypatch, case_session, text):
    def extract(text, *, context):
        assert context["phase"] == "POST_PROCESS"
        return TurnExtraction(scope="in_scope")
    monkeypatch.setattr(intake, "extract_turn_with_llm", extract)
    email = Mock(return_value={"status": "approved"})
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    state = graph.invoke({"user_message": text}, config=case_session)
    assert state["email_consent"] is None
    assert state["email_offer_pending"] is True
    assert 'click "Yes, send summary"' in state["assistant_message"]
    email.assert_not_called()


def test_unknown_request_does_not_reuse_stale_action(monkeypatch, case_session):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *args, **kwargs: TurnExtraction(scope="in_scope"))
    query = Mock()
    email = Mock()
    monkeypatch.setattr("claim_agent.services.case_harness.tools.get_claim_for_action", query)
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    graph.invoke({"user_message": "I'm not sure what I need."}, config=case_session)
    query.assert_not_called()
    email.assert_not_called()


def test_model_unavailable_after_verification_stops_all_actions(monkeypatch, case_session):
    monkeypatch.setattr(intake, "extract_turn_with_llm", Mock(side_effect=llm.LLMUnavailable()))
    email = Mock()
    query = Mock()
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    monkeypatch.setattr("claim_agent.services.case_harness.tools.get_claim_for_action", query)
    state = graph.invoke({"user_message": "Yes, send it."}, config=case_session)
    assert state["llm_available"] is False
    assert state["llm_error"] is True
    assert state["phase"] == "POST_PROCESS"
    query.assert_not_called()
    email.assert_not_called()


def test_api_missing_token_shows_unavailable_without_verification(monkeypatch):
    # Exercise the real model boundary with no configured model, not the fixture.
    monkeypatch.setattr(intake, "extract_turn_with_llm", llm.extract_turn_with_llm)
    with TestClient(app) as client:
        assert client.get("/api/llm-status").json()["status"] == "unavailable"
        response = client.post("/api/chat", json={"message": (
            "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472."
        )})
    assert response.status_code == 503
    data = response.json()
    assert data["llm_status"] == "unavailable"
    assert data["phase"] == "VERIFY_ID" and data["verified"] is False
    assert data["case_tool_calls"] == 0 and data["claim_id"] is None


def test_model_adapter_failures_are_sanitized(monkeypatch):
    monkeypatch.setattr(llm, "get_llm", Mock(side_effect=ValueError("secret-token")))
    with pytest.raises(llm.LLMUnavailable) as err:
        llm.extract_turn_with_llm("hello")
    assert "secret-token" not in str(err.value)


def test_real_extractor_passes_email_context(monkeypatch):
    invoke = Mock(side_effect=[IdentityExtraction(), TurnUnderstanding(scope="in_scope")])
    model = Mock()
    model.with_structured_output.return_value.invoke = invoke
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    answer = llm.extract_turn_with_llm("sounds good", context={"email_offer_pending": True, "phase": "POST_PROCESS"})
    assert "email_consent" not in answer.model_dump()
    assert "exclusively by UI buttons" in invoke.call_args.args[0][0][1]
    assert '"email_offer_pending": true' in invoke.call_args.args[0][1][1]


def test_early_question_reaches_harness_after_later_identity(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    question = "Why was my healthcare claim from January denied?"
    initial = graph.invoke({"user_message": question}, config=config)
    assert initial["phase"] == "VERIFY_ID"
    assert initial.get("selected_claim_id") is None
    original = llm.plan_case_with_llm
    planner = Mock(side_effect=original)
    monkeypatch.setattr(llm, "plan_case_with_llm", planner)
    state = graph.invoke({"user_message": "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472."}, config=config)
    assert state["selected_claim_id"] == "CL-2048"
    assert state["harness_status"] == "completed"
    assert planner.call_args.args[0] == question

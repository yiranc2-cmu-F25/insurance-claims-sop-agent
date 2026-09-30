"""Non-claim turns after routing get natural wording; claim facts never come from it."""
from unittest.mock import Mock
from uuid import uuid4

import pytest

from claim_agent.llm import client as llm
from claim_agent.llm.schemas import TurnExtraction
from claim_agent.services import conversation_reply
from claim_agent.services.email_followup import EMAIL_PROMPT
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


@pytest.fixture
def case_session():
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.invoke({"user_message": (
        "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
        "What is the status of my denied healthcare claim from January?"
    )}, config=config)
    return config


def test_process_question_after_verification_is_answered_before_the_email_instruction(monkeypatch, case_session):
    calls = []
    monkeypatch.setattr(llm, "compose_conversation_reply", lambda facts: calls.append(facts) or Mock(
        reply="Good question: we confirm identity first because claim details are protected, and only the last four digits of an ID are ever requested."))
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", dialogue_act="conversation"))
    state = graph.invoke({"user_message": "Why do you need my date of birth? Is it safe?"}, config=case_session)
    assert state["assistant_message"].startswith("Good question") and state["assistant_message"].endswith(EMAIL_PROMPT)
    assert calls[-1]["current_claim_id"] == "CL-2048" and calls[-1]["identity_verified"] is True
    assert "Margaret" not in repr(calls[-1]) and "4472" not in repr(calls[-1])
    assert state["email_offer_pending"] and state["case_tool_calls"] == 0 and state["email_consent"] is None


@pytest.mark.parametrize("bad", [
    "Your claim was denied for missing documents.",
    "Call us at 6505551234.",
    "Write to help@example.com.",
    "See policy POL-9921.",
    "",
])
def test_off_script_wording_falls_back_to_the_fixed_instruction(monkeypatch, case_session, bad):
    monkeypatch.setattr(llm, "compose_conversation_reply", lambda facts: Mock(reply=bad))
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope"))
    state = graph.invoke({"user_message": "Thanks for your help."}, config=case_session)
    assert state["assistant_message"] == EMAIL_PROMPT


def test_email_replies_and_model_failures_keep_the_fixed_instruction(monkeypatch, case_session):
    composer = Mock()
    monkeypatch.setattr(llm, "compose_conversation_reply", composer)
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", dialogue_act="email_reply"))
    state = graph.invoke({"user_message": "Yes, send it."}, config=case_session)
    assert state["assistant_message"] == EMAIL_PROMPT and state["email_consent"] is None
    composer.assert_not_called()
    monkeypatch.setattr(llm, "compose_conversation_reply", Mock(side_effect=llm.LLMUnavailable()))
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope"))
    state = graph.invoke({"user_message": "Thanks!"}, config=case_session)
    assert state["assistant_message"] == EMAIL_PROMPT and state["llm_available"] is True


def test_unknown_request_in_resolve_phase_uses_the_composer_with_the_menu_as_fallback(monkeypatch, case_session):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", request_mode="cancel"))
    state = graph.invoke({"user_message": "Cancel my pending questions."}, config=case_session)
    assert state["phase"] == "RESOLVE_INTENT"
    monkeypatch.setattr(llm, "compose_conversation_reply", lambda facts: Mock(reply="You're welcome. Ask me anything about your claim whenever you're ready."))
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope"))
    state = graph.invoke({"user_message": "Thanks, that's all for now."}, config=case_session)
    assert state["assistant_message"].startswith("You're welcome") and state["case_tool_calls"] == 0
    monkeypatch.setattr(llm, "compose_conversation_reply", Mock(side_effect=llm.LLMUnavailable()))
    state = graph.invoke({"user_message": "Hello?"}, config=case_session)
    assert "Which would you like to know?" in state["assistant_message"]


def test_acceptable_allows_only_the_current_claim_id():
    assert conversation_reply.acceptable("I can look into CL-2048 whenever you ask.", "CL-2048")
    assert not conversation_reply.acceptable("I can look into CL-2011 too.", "CL-2048")
    assert not conversation_reply.acceptable("Your claim has been approved.", "CL-2048")

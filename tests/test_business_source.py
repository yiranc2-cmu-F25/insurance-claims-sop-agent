"""Source-bound memory, field semantics, and dialogue-act isolation."""
from uuid import uuid4

import pytest

from claim_agent.guardrails.business_source import source_checked_business
from claim_agent.guardrails.identity_source import source_checked_identity
from claim_agent.llm.schemas import ClaimQuestion, HintSources, IdentityExtraction, TurnUnderstanding, TurnExtraction
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


@pytest.mark.parametrize("field", ["name", "id_last4", "policy_number", "represented_policy_number"])
def test_claim_reference_is_never_personal_identity(field):
    message = "For CL-2048, can I provide an alternative report?"
    assert source_checked_identity(message, IdentityExtraction(**{field: "CL-2048"})) == {}
    assert source_checked_identity(message, IdentityExtraction(id_last4="2048")) == {}


def test_policy_is_not_claim_and_missing_year_is_not_invented():
    message = "Policy POL-9921. Why was my healthcare claim from January denied?"
    extracted = TurnUnderstanding(
        intent="denial_question", dialogue_act="claim_request", case_id="POL-9921", year="2023",
        requests=[ClaimQuestion(intent="denial_question", question="Why was my claim denied in 2023?",
                                source="Why was my healthcare claim from January denied?",
                                case_id="POL-9921", year="2023", month="January")],
    )
    result = source_checked_business(message, extracted, IdentityExtraction(policy_number="POL-9921"))
    assert result["case_id"] is None and result["year"] is None
    question = result["requests"][0]
    assert question["case_id"] is None and question["year"] is None
    assert question["month"] == "January" and "2023" not in question["question"]


def test_dob_year_cannot_become_claim_year():
    message = "My DOB is March 15, 1985. Why was my January claim denied?"
    result = source_checked_business(message, TurnUnderstanding(year="1985"), IdentityExtraction(dob="March 15, 1985"))
    assert result["year"] is None
    result = source_checked_business(message + " The claim was from 1985.", TurnUnderstanding(year="1985"), IdentityExtraction(dob="March 15, 1985"))
    assert result["year"] == "1985"


def test_semantic_hint_requires_literal_source_not_historical_text():
    result = source_checked_business("My medical claim was rejected.", TurnUnderstanding(
        case_type="healthcare", status="denied", month="January",
        hint_sources=HintSources(case_type="medical", status="rejected", month="January"),
    ), IdentityExtraction())
    assert result["case_type"] == "healthcare" and result["status"] == "denied"
    assert result["month"] is None


def test_claim_reference_survives_identity_extractors_wrong_field():
    message = "For CL-2048, can I provide an alternative report?"
    result = source_checked_business(message, TurnUnderstanding(case_id="CL-2048", requests=[
        ClaimQuestion(intent="document_submission", question=message, case_id="CL-2048"),
    ]), IdentityExtraction(policy_number="CL-2048"))
    assert result["case_id"] == "CL-2048" and "CL-2048" in result["requests"][0]["question"]


def test_claim_identifier_casing_is_not_a_semantic_change():
    result = source_checked_business("Please check cl-2048.", TurnUnderstanding(case_id="CL-2048"), IdentityExtraction())
    assert result["case_id"] == "CL-2048"


@pytest.mark.parametrize("act", ["email_reply", "identity_reply"])
def test_non_business_reply_cannot_recreate_historical_questions(act):
    result = source_checked_business("Yes, send it.", TurnUnderstanding(
        dialogue_act=act, intent="document_submission", new_case=True, year="2023", case_id="CL-2048",
        request_mode="replace", document_alternatives_exhausted=True,
        requests=[ClaimQuestion(intent="document_submission", question="What documents do I need?", source="Yes, send it.")],
    ), IdentityExtraction())
    assert result["intent"] == "unknown" and not result["requests"]
    assert result["case_id"] is None and result["year"] is None
    assert not result["new_case"] and result["request_mode"] == "append"
    assert result["document_alternatives_exhausted"] is None


def test_mixed_email_reply_keeps_actual_claim_question():
    message = "Yes, send it. What documents do I need?"
    result = source_checked_business(message, TurnUnderstanding(dialogue_act="claim_request", requests=[
        ClaimQuestion(intent="document_submission", question="What documents do I need?", source="What documents do I need?"),
    ]), IdentityExtraction())
    assert len(result["requests"]) == 1


def test_unsourced_new_question_is_not_replaced_with_an_intent_fallback():
    result = source_checked_business("Thanks.", TurnUnderstanding(intent="document_submission", requests=[
        ClaimQuestion(intent="document_submission", question="What documents do I need?", source="old question"),
    ]), IdentityExtraction())
    assert not result["requests"] and result["intent"] == "unknown"


def test_bare_stale_intent_cannot_create_a_request_from_greeting():
    result = source_checked_business("Thanks.", TurnUnderstanding(intent="denial_question"), IdentityExtraction())
    assert result["intent"] == "unknown" and not result["requests"]


def test_short_followup_keeps_current_turn_constraints():
    message = "The lab cannot reissue anything, and I have no scan or copy. What now?"
    result = source_checked_business(message, TurnUnderstanding(requests=[
        ClaimQuestion(intent="document_submission", question="What now?", source="What now?"),
    ]), IdentityExtraction())
    assert result["requests"][0]["question"] == message


def test_memory_switch_removes_previous_case_search_filters(monkeypatch):
    session = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        name="Margaret Chen", dob="1985-03-15", id_last4="4472", intent="denial_question",
        case_id="CL-2048", year="2026", status="denied", month="January", scope="in_scope"))
    first = graph.invoke({"user_message": "My claim question"}, session)
    assert first["phase"] == "POST_PROCESS"
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        intent="status_inquiry", case_id="CL-2011", new_case=True, scope="in_scope"))
    second = graph.invoke({"user_message": "Now claim CL-2011"}, session)
    assert second["intent_hint"] == {"case_id": "CL-2011"}
    assert second["selected_claim_id"] == "CL-2011"
    assert set(second["case_memories"]) == {"CL-2048", "CL-2011"}
    assert "CL-2048" not in second["email_offer"]["summary"]


def test_legacy_bad_hints_are_removed_from_active_and_pending_memory(monkeypatch):
    contexts = []
    def extract(*args, context):
        contexts.append(context)
        return TurnExtraction(scope="in_scope")
    monkeypatch.setattr(intake, "extract_turn_with_llm", extract)
    hints = {"case_id": "POL-9921", "year": "2023", "month": "January"}
    result = intake.capture_turn({"user_message": "Hello", "intent_hint": hints,
                                 "pending_requests": [{"id": "q1", "intent": "denial_question", "question": "Why denied?", "hints": hints}]})
    assert contexts[0]["claim_hints"] == {"month": "January"}
    assert result["intent_hint"] == {"month": "January"}
    assert result["pending_requests"][0]["hints"] == {"month": "January"}
    assert result["hint_schema_version"] == 1

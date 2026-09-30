"""Explicit model responses exercise correction and backlog semantics, not keywords."""
from unittest.mock import Mock
from uuid import uuid4

import pytest

from claim_agent.llm import client as llm
from claim_agent.llm.schemas import CasePlan, GroundedAnswer, TurnExtraction
from claim_agent.services import verification_session as auth
from claim_agent.services.email_followup import choose_email
from claim_agent.services.identity_corrections import apply_identity_input
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph

OWNER = dict(name="Margaret Chen", dob="1985-03-15", id_last4="4472")


@pytest.fixture
def turn(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    def run(**fields):
        monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", **fields))
        return graph.invoke({"user_message": "Test customer's current response"}, config=config)
    return run


def questions(*intents, case_id="CL-2048"):
    return [{"intent": intent, "question": "Please help with " + intent, "case_id": case_id} for intent in intents]


def test_correction_requires_confirmation_then_reverifies(turn):
    first = turn(name="Margaret Chen", dob="1985-03-16", id_last4="4472", intent="status_inquiry", case_id="CL-2048")
    assert not first.get("verified_party_id") and first["verification_failed_attempts"] == 1
    proposed = turn(dob="1985-03-15")
    assert proposed["collected_pii"]["dob"] == "1985-03-16"
    assert proposed["pending_identity_changes"] == {"dob": "1985-03-15"}
    assert "confirm" in proposed["assistant_message"] and not proposed.get("selected_claim_id")
    confirmed = turn(identity_correction="confirm")
    assert confirmed["collected_pii"]["dob"] == "1985-03-15"
    assert confirmed["verified_party_id"] == "P9" and confirmed["phase"] == "POST_PROCESS"
    assert not confirmed["pending_identity_changes"]


def test_correction_revokes_old_offer_and_rejection_keeps_original(turn):
    first = turn(**OWNER, intent="status_inquiry", case_id="CL-2048")
    offer_id = first["email_offer"]["id"]
    pending = turn(dob="1985-03-16")
    assert not pending["verified_party_id"] and not pending["email_offer"]
    with pytest.raises(ValueError):
        choose_email(pending, offer_id, "send")
    rejected = turn(identity_correction="reject")
    assert rejected["collected_pii"]["dob"] == OWNER["dob"]
    assert rejected["verified_party_id"] == "P9" and not rejected["pending_identity_changes"]


def test_confirm_cannot_accept_a_different_new_proposal_or_reset_failure_budget():
    state = {"collected_pii": {"dob": "1985-03-16"}, "pending_identity_changes": {"dob": "1985-03-15"},
             "verification_failed_attempts": 4}
    update = apply_identity_input(state, {"dob": "1985-03-17"}, {}, "confirm")
    assert update["collected_pii"]["dob"] == "1985-03-16"
    assert update["pending_identity_changes"]["dob"] == "1985-03-17"
    assert "verification_failed_attempts" not in update
    changed_role = apply_identity_input(state, {}, {}, "none", role_changed=True)
    assert "verification_failed_attempts" not in changed_role


def test_multiple_early_questions_survive_partial_verification(turn):
    items = questions("denial_question", "document_submission", "appeal_deadline")
    early = turn(requests=items)
    assert len(early["pending_requests"]) == 3 and early["phase"] == "VERIFY_ID"
    assert "pathology" not in early["assistant_message"]
    partial = turn(name=OWNER["name"])
    assert len(partial["pending_requests"]) == 3
    result = turn(dob=OWNER["dob"], id_last4=OWNER["id_last4"])
    assert not result["pending_requests"] and len(result["completed_requests"]) == 3
    assert result["phase"] == "POST_PROCESS" and result["case_tool_calls"] == 4
    assert "pathology" in result["assistant_message"] and "2026-03-18" in result["assistant_message"]
    assert result["email_offer_pending"]


def test_batch_bound_and_continue_without_replaying_completed_items(turn):
    items = questions("status_inquiry", "denial_question", "document_submission", "appeal_deadline")
    result = turn(**OWNER, requests=items)
    assert len(result["completed_requests"]) == 3 and len(result["pending_requests"]) == 1
    assert not result["email_offer_pending"] and result["harness_status"] == "pending_questions"
    finished_ids = [item["id"] for item in result["completed_requests"]]
    result = turn(request_mode="continue")
    assert len(result["completed_requests"]) == 4 and result["case_tool_calls"] == 1
    assert finished_ids == [item["id"] for item in result["completed_requests"][:3]]
    assert result["phase"] == "POST_PROCESS"


def test_partial_failure_retains_only_unfinished_questions(monkeypatch, turn):
    original = llm.plan_case_with_llm
    def plan(question, intent, allowed):
        if intent == "denial_question":
            raise llm.LLMUnavailable()
        return original(question, intent, allowed)
    monkeypatch.setattr(llm, "plan_case_with_llm", plan)
    result = turn(**OWNER, requests=questions("status_inquiry", "denial_question", "appeal_deadline"))
    assert result["harness_status"] == "llm_unavailable" and not result["email_offer_pending"]
    assert len(result["completed_requests"]) == 1 and len(result["pending_requests"]) == 2
    monkeypatch.setattr(llm, "plan_case_with_llm", original)
    result = turn(request_mode="continue")
    assert len(result["completed_requests"]) == 3 and result["case_tool_calls"] == 2


def test_case_clarification_applies_to_same_case_questions(turn):
    early = turn(**OWNER, case_type="healthcare", month="January", requests=[
        {"intent": intent, "question": intent} for intent in ("status_inquiry", "appeal_deadline")])
    assert "multiple claims" in early["assistant_message"]
    result = turn(case_id="CL-2048")
    assert len(result["completed_requests"]) == 2 and not result["pending_requests"]


def test_new_case_question_does_not_discard_earlier_pending_questions(turn):
    turn(requests=questions("status_inquiry", "denial_question"))
    pending = turn(new_case=True, case_id="CL-2011", requests=questions("payment_question", case_id="CL-2011"))
    assert len(pending["pending_requests"]) == 3
    result = turn(**OWNER)
    assert [q["claim_id"] for q in result["completed_requests"]] == ["CL-2048", "CL-2048", "CL-2011"]
    assert "CL-2048" not in result["email_offer"]["summary"]


def test_each_queued_case_rechecks_ownership(turn):
    result = turn(**OWNER, requests=questions("status_inquiry") + questions("denial_question", case_id="CL-3001"))
    assert len(result["completed_requests"]) == 1 and len(result["pending_requests"]) == 1
    assert "diagnosis report" not in result["assistant_message"]
    assert not result["email_offer_pending"] and result["case_tool_calls"] == 1


def test_replace_and_cancel_are_explicit(turn):
    turn(requests=questions("status_inquiry", "denial_question"))
    replaced = turn(request_mode="replace", requests=questions("appeal_deadline"))
    assert [q["intent"] for q in replaced["pending_requests"]] == ["appeal_deadline"]
    cancelled = turn(request_mode="cancel")
    assert not cancelled["pending_requests"] and cancelled["requested_intent"] == "unknown"
    assert cancelled["phase"] == "VERIFY_ID"


def test_expiry_during_batch_withholds_answers_and_keeps_all_undelivered_questions(monkeypatch, turn):
    clock = [10000.0]
    monkeypatch.setattr(auth, "time", lambda: clock[0])
    original = llm.review_case_answer
    def slow_review(*args):
        clock[0] += auth.MAX_AGE_SECONDS
        return original(*args)
    monkeypatch.setattr(llm, "review_case_answer", slow_review)
    result = turn(**OWNER, requests=questions("status_inquiry", "denial_question"))
    assert result["phase"] == "VERIFY_ID" and not result["verified_party_id"]
    assert "CL-2048" not in result["assistant_message"] and "expired" in result["assistant_message"]
    assert len(result["pending_requests"]) == 2 and not result.get("completed_requests")
    assert not result["email_offer_pending"]


def _clarify_general_questions(monkeypatch):
    original = llm.plan_case_with_llm
    def plan(question, intent, allowed):
        if intent == "general_claim_question":
            return CasePlan(decision="clarify", tools=[], followup_topic=None)
        return original(question, intent, allowed)
    monkeypatch.setattr(llm, "plan_case_with_llm", plan)


def test_unclear_question_is_superseded_by_the_next_question(monkeypatch, turn):
    _clarify_general_questions(monkeypatch)
    result = turn(**OWNER, requests=questions("general_claim_question"))
    assert result["harness_status"] == "clarification_needed" and not result["email_offer_pending"]
    assert result["pending_requests"][0]["awaiting_caller"]
    result = turn(requests=questions("appeal_deadline"))
    assert not result["pending_requests"] and result["phase"] == "POST_PROCESS"
    assert "2026-03-18" in result["assistant_message"]
    assert [q["intent"] for q in result["completed_requests"]] == ["appeal_deadline"]


def test_other_questions_are_answered_in_the_same_turn_as_a_clarification(monkeypatch, turn):
    _clarify_general_questions(monkeypatch)
    result = turn(**OWNER, requests=questions("general_claim_question", "appeal_deadline"))
    assert "2026-03-18" in result["assistant_message"]
    assert [q["intent"] for q in result["completed_requests"]] == ["appeal_deadline"]
    assert [q["intent"] for q in result["pending_requests"]] == ["general_claim_question"]
    assert not result["email_offer_pending"] and "still pending" not in result["assistant_message"]
    result = turn(request_mode="continue")
    assert not result["pending_requests"] and result["case_tool_calls"] == 0


def test_clarification_answer_refreshes_the_unclear_question(turn):
    early = turn(**OWNER, case_type="healthcare", month="January", requests=[{"intent": "status_inquiry", "question": "status"}])
    assert "multiple claims" in early["assistant_message"] and early["pending_requests"][0]["awaiting_caller"]
    result = turn(case_id="CL-2048")
    assert not result["pending_requests"] and result["harness_status"] == "completed"
    assert result["completed_requests"][-1]["claim_id"] == "CL-2048"


def test_transient_failure_is_retried_after_the_next_question_not_before(monkeypatch, turn):
    original = llm.plan_case_with_llm
    calls = {"count": 0}
    def plan(question, intent, allowed):
        if intent == "denial_question" and calls["count"] == 0:
            calls["count"] += 1
            raise llm.LLMUnavailable()
        return original(question, intent, allowed)
    monkeypatch.setattr(llm, "plan_case_with_llm", plan)
    result = turn(**OWNER, requests=questions("denial_question"))
    assert result["harness_status"] == "llm_unavailable" and result["pending_requests"][0]["retry"]
    result = turn(requests=questions("appeal_deadline"))
    assert [q["intent"] for q in result["completed_requests"]] == ["appeal_deadline", "denial_question"]
    assert not result["pending_requests"] and result["phase"] == "POST_PROCESS"


def test_blocked_answer_does_not_block_the_next_question(monkeypatch, turn):
    monkeypatch.setattr(llm, "compose_case_answer", lambda *a: GroundedAnswer(
        answer="You will receive 99999.00.", sources=["get_claim_for_action"]))
    result = turn(**OWNER, requests=questions("status_inquiry"))
    assert result["harness_status"] == "output_blocked" and result["pending_requests"][0]["awaiting_caller"]
    monkeypatch.setattr(llm, "compose_case_answer", lambda *a: GroundedAnswer(
        answer="The appeal deadline is 2026-03-18.", sources=["get_claim_for_action"]))
    result = turn(requests=questions("appeal_deadline"))
    assert [q["intent"] for q in result["completed_requests"]] == ["appeal_deadline"]
    assert not result["pending_requests"] and result["email_offer_pending"]


def test_unmatched_claim_number_is_not_carried_into_the_next_question(turn):
    result = turn(**OWNER, requests=questions("status_inquiry", case_id="CL-3001"))
    assert "accessible claim" in result["assistant_message"] and "case_id" not in result["intent_hint"]
    result = turn(requests=[{"intent": "appeal_deadline", "question": "When is the appeal deadline?"}],
                  case_type="healthcare", status="denied")
    assert result["completed_requests"][-1]["claim_id"] == "CL-2048" and "2026-03-18" in result["assistant_message"]


def test_duplicate_wording_keeps_the_specific_intent_only(turn):
    text = "For claim CL-2011, how much did the insurer actually pay, and what does net_fee mean?"
    result = turn(**OWNER, requests=[
        {"intent": "payment_question", "question": text, "case_id": "CL-2011"},
        {"intent": "general_claim_question", "question": text, "case_id": "CL-2011"},
    ])
    assert [q["intent"] for q in result["completed_requests"]] == ["payment_question"]
    assert not result["pending_requests"] and result["phase"] == "POST_PROCESS"


def test_a_second_person_cannot_take_over_a_verified_conversation(turn):
    first = turn(**OWNER, intent="denial_question", case_id="CL-2048")
    assert first["phase"] == "POST_PROCESS"
    # A delegate starts typing in the same browser conversation.
    state = turn(caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
                 represented_policy_number="POL-9921")
    assert "new conversation" in state["assistant_message"].lower()
    assert not state.get("verified_party_id") and state["phase"] == "VERIFY_ID" and state["case_tool_calls"] == 0
    assert state["collected_pii"] == {} and not state["pending_requests"] and state["email_offer"] is None
    state = turn(caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
                 represented_policy_number="POL-9921")
    assert not state.get("verified_party_id") and "new conversation" in state["assistant_message"].lower()
    # The original caller can verify again and continue.
    state = turn(**OWNER, intent="appeal_deadline", case_id="CL-2048")
    assert state["verified_party_id"] == "P9" and "2026-03-18" in state["assistant_message"]


def test_same_role_takeover_via_confirmed_correction_is_refused(turn):
    turn(**OWNER, intent="denial_question", case_id="CL-2048")
    proposed = turn(name="Ma Tian", dob="1964-09-10", id_last4="6688", id_type="national_id_last4")
    assert proposed["pending_identity_changes"] and not proposed.get("verified_party_id")
    state = turn(identity_correction="confirm")
    assert not state.get("verified_party_id") and "new conversation" in state["assistant_message"].lower()
    assert state["case_tool_calls"] == 0 and "diagnosis" not in state["assistant_message"]
    state = turn(intent="denial_question")
    assert not state.get("verified_party_id") and state["case_tool_calls"] == 0
    state = turn(**OWNER)
    assert state["verified_party_id"] == "P9"


def test_typo_fix_by_the_same_customer_keeps_the_pending_question(turn):
    turn(name="Margaret Chen", dob="1985-03-16", id_last4="4472", intent="appeal_deadline", case_id="CL-2048")
    turn(dob="1985-03-15")
    state = turn(identity_correction="confirm")
    assert state["verified_party_id"] == "P9" and "2026-03-18" in state["assistant_message"]


def test_clarifications_follow_the_answers_in_a_mixed_reply(monkeypatch, turn):
    _clarify_general_questions(monkeypatch)
    result = turn(**OWNER, requests=questions("general_claim_question", "appeal_deadline"))
    answer_at = result["assistant_message"].index("2026-03-18")
    clarification_at = result["assistant_message"].index("About the rest of your message")
    assert answer_at < clarification_at


def test_refused_switch_does_not_keep_the_other_persons_role_declaration(turn):
    turn(**OWNER, intent="denial_question", case_id="CL-2048")
    state = turn(caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
                 represented_policy_number="POL-9921")
    assert "new conversation" in state["assistant_message"].lower()
    assert state["security_declared_role"] == "policyholder" and state["caller_role"] == "policyholder"

import json
from time import time
from unittest.mock import Mock

import pytest

from claim_agent.services import case_harness as harness
from claim_agent.llm import client as llm
from claim_agent.llm.schemas import AnswerReview, CasePlan, GroundedAnswer


@pytest.fixture
def verified_case():
    now = time()
    return {
        "verified_at": now, "verified_last_active_at": now,
        "phase": "PROCESS_CASE", "llm_available": True,
        "verified_party_id": "P9", "selected_claim_id": "CL-2048",
        "verification_matches": ["name", "dob", "id_last4"],
        "resolved_intent": "status_inquiry", "authorized_action": "read_claim_status",
        "scope": "in_scope", "security_risk": "low", "security_status": "cleared", "authorization_denied": False,
        "user_message": "What is my claim status?",
    }


@pytest.mark.parametrize("change", [
    {"verified_party_id": None}, {"verification_matches": ["name", "dob"]},
    {"phase": "VERIFY_ID"}, {"authorized_action": "send_email_summary"},
    {"authorized_action": "read_denial_reason"}, {"authorization_denied": True},
    {"scope": "out_of_scope"}, {"security_risk": "high"},
    {"security_status": None}, {"security_status": "unavailable"},
    {"security_status": "clarification_required"}, {"security_risk": "medium"},
])
def test_boundary_denies_before_any_tool(monkeypatch, verified_case, change):
    query = Mock()
    planner = Mock()
    monkeypatch.setattr(harness.tools, "get_claim_for_action", query)
    monkeypatch.setattr(llm, "plan_case_with_llm", planner)
    result = harness.run_case({**verified_case, **change})
    assert result["harness_status"] != "completed"
    assert result["case_tool_calls"] == 0
    planner.assert_not_called()
    query.assert_not_called()


@pytest.mark.parametrize("names,expected", [
    (["get_claim_for_action"] * 4, "tool_budget_exceeded"),
    (["get_claim_for_action"] * 2, "duplicate_tool"),
    (["get_claim_for_action", "get_claim_field_definitions"], "tool_not_allowed"),
    (["send_email_summary"], "invalid_model_output"),
])
def test_invalid_plan_has_zero_tool_calls(monkeypatch, verified_case, names, expected):
    query = Mock()
    monkeypatch.setattr(harness.tools, "get_claim_for_action", query)
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: {
        "decision": "execute", "tools": names, "followup_topic": None,
    })
    result = harness.run_case(verified_case)
    assert result["harness_status"] == expected
    query.assert_not_called()


def test_model_cannot_supply_another_customer(monkeypatch, verified_case):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: {
        "decision": "execute", "tools": ["get_claim_for_action"],
        "followup_topic": None, "verified_party_id": "P12",
    })
    result = harness.run_case(verified_case)
    assert result["case_tool_calls"] == 0
    assert result["harness_status"] == "invalid_model_output"


def test_cross_customer_claim_not_returned(monkeypatch, verified_case):
    composer = Mock()
    monkeypatch.setattr(llm, "compose_case_answer", composer)
    result = harness.run_case({**verified_case, "selected_claim_id": "CL-3001"})
    assert result["harness_status"] == "claim_not_accessible"
    assert "CL-3001" not in result["reply"]
    composer.assert_not_called()


def test_tool_exception_stops_and_does_not_leak(monkeypatch, verified_case):
    query = Mock(side_effect=TimeoutError("secret PII and token sk-private"))
    composer = Mock()
    monkeypatch.setattr(harness.tools, "get_claim_for_action", query)
    monkeypatch.setattr(llm, "compose_case_answer", composer)
    result = harness.run_case(verified_case)
    assert result["harness_status"] == "tool_failed"
    assert result["case_tool_calls"] == 1
    assert "sk-private" not in json.dumps(result)
    composer.assert_not_called()
    assert query.call_count == 1  # No automatic retries.


@pytest.mark.parametrize("text,sources", [
    ("Claim CL-3001 is approved.", ["get_claim_for_action"]),
    ("You will receive $99999.00.", ["get_claim_for_action"]),
    ("The deadline is 2099-01-01.", ["get_claim_for_action"]),
    ("The documents are ready.", ["get_document_guidance_for_claim"]),
])
def test_unsupported_output_never_reaches_user(monkeypatch, verified_case, text, sources):
    monkeypatch.setattr(llm, "compose_case_answer", lambda *args: GroundedAnswer(answer=text, sources=sources))
    result = harness.run_case(verified_case)
    assert result["harness_status"] == "output_blocked"
    assert text not in result["reply"]


def test_semantic_review_rejects_invented_reason(monkeypatch, verified_case):
    bad = "Your claim was denied because your policy expired."
    monkeypatch.setattr(llm, "compose_case_answer", lambda *args: GroundedAnswer(answer=bad, sources=["get_claim_for_action"]))
    monkeypatch.setattr(llm, "review_case_answer", lambda *args: AnswerReview(
        supported_by_evidence=False, answers_question=True, no_unauthorized_actions=True,
    ))
    result = harness.run_case(verified_case)
    assert result["harness_status"] == "output_blocked"
    assert bad not in result["reply"]


@pytest.mark.parametrize("stage,calls", [("plan_case_with_llm", 0), ("compose_case_answer", 1), ("review_case_answer", 1)])
def test_model_failure_is_closed_at_every_stage(monkeypatch, verified_case, stage, calls):
    monkeypatch.setattr(llm, stage, Mock(side_effect=llm.LLMUnavailable()))
    result = harness.run_case(verified_case)
    assert result["harness_status"] == "llm_unavailable"
    assert result["case_tool_calls"] == calls
    assert "denied" not in result["reply"]


def test_three_tool_plan_and_audit(monkeypatch, verified_case):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: CasePlan(
        decision="execute", tools=["get_claim_for_action", "get_document_guidance_for_claim", "get_claim_followup_guidance"],
        followup_topic="processing_time_after_submission",
    ))
    result = harness.run_case({**verified_case,
        "resolved_intent": "document_submission", "authorized_action": "read_document_guidance",
        "user_message": "What documents should I send? How long will review take?",
    })
    assert result["harness_status"] == "completed"
    assert result["case_tool_calls"] == 3
    assert len([e for e in result["audit_events"] if e["event"] == "tool"]) == 3
    assert result["audit_events"][-1]["outcome"] == "approved"
    audit = json.dumps(result["audit_events"])
    assert "CL-2048" not in audit and "P9" not in audit and "pathology" not in audit


@pytest.mark.parametrize("names", [[], ["get_document_guidance_for_claim"],
    ["get_document_guidance_for_claim", "get_claim_followup_guidance"],
    ["get_document_guidance_for_claim", "get_claim_for_action"],
])
def test_harness_owns_required_read_order_even_when_model_omits_it(monkeypatch, verified_case, names):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: CasePlan(
        decision="execute", tools=names,
        followup_topic="missing_required_material_alternatives" if "get_claim_followup_guidance" in names else None,
    ))
    result = harness.run_case({**verified_case, "resolved_intent": "document_submission",
                              "authorized_action": "read_document_guidance"})
    assert result["harness_status"] == "completed"
    reads = [event["tool"] for event in result["audit_events"] if event["event"] == "tool"]
    assert reads[:2] == ["get_claim_for_action", "get_document_guidance_for_claim"]
    assert 2 <= len(reads) <= 3 and len(set(reads)) == len(reads)


def test_payment_field_definitions_are_mandatory_in_harness(monkeypatch, verified_case):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: CasePlan(
        decision="execute", tools=["get_claim_for_action"], followup_topic=None,
    ))
    result = harness.run_case({**verified_case, "resolved_intent": "payment_question", "authorized_action": "read_claim_amounts"})
    assert result["harness_status"] == "completed" and result["case_tool_calls"] == 2


def test_prerequisites_do_not_convert_model_human_decision_into_execution(monkeypatch, verified_case):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *args: CasePlan(decision="human", tools=[], followup_topic=None))
    result = harness.run_case(verified_case)
    assert result["harness_status"] == "human_required" and result["case_tool_calls"] == 0


def test_recorded_dates_may_be_spelled_out_but_not_changed():
    evidence = {"get_claim_for_action": {"case_id": "CL-2048", "appeal_deadline": "2026-03-18"}}
    sources = list(evidence)
    for text in ("The appeal deadline is March 18, 2026.", "File by 18 March 2026.", "Deadline: Mar 18th, 2026."):
        harness._validate_answer(GroundedAnswer(answer=text, sources=sources), evidence, "CL-2048")
    for text in ("The appeal deadline is March 19, 2026.", "File by 18 March 2027.", "Deadline: Feb 30, 2026."):
        with pytest.raises(harness.HarnessBlocked):
            harness._validate_answer(GroundedAnswer(answer=text, sources=sources), evidence, "CL-2048")


def test_unknown_source_names_are_ignored_but_real_evidence_must_be_cited():
    evidence = {"get_claim_for_action": {"case_id": "CL-2048", "status": "denied"}}
    harness._validate_answer(GroundedAnswer(answer="It is denied.", sources=["get_claim_for_action", "get_claim_field_definitions"]), evidence, "CL-2048")
    with pytest.raises(harness.HarnessBlocked):
        harness._validate_answer(GroundedAnswer(answer="It is denied.", sources=["get_claim_field_definitions"]), evidence, "CL-2048")


def test_numbered_list_is_formatting_but_business_numbers_are_still_checked():
    evidence = {"get_document_guidance_for_claim": {"documents": ["pathology report", "office note"]}}
    sources = list(evidence)
    harness._validate_answer(GroundedAnswer(answer="1. Pathology report\n2. Office note", sources=sources), evidence, "CL-2048")
    for text in ("1. You will receive 99999.00 USD.", "2. The deadline is 2099-01-01.", "1. Read claim CL-3001."):
        with pytest.raises(harness.HarnessBlocked):
            harness._validate_answer(GroundedAnswer(answer=text, sources=sources), evidence, "CL-2048")


def test_exhausted_documents_use_fixed_authorized_manual_review_path(monkeypatch, verified_case):
    planner = Mock()
    monkeypatch.setattr(llm, "plan_case_with_llm", planner)
    result = harness.run_case({**verified_case, "resolved_intent": "document_submission",
                              "authorized_action": "read_document_guidance", "document_alternatives_exhausted": True})
    assert result["harness_status"] == "completed" and result["documents_exhausted"]
    assert result["case_tool_calls"] == 2
    planner.assert_not_called()
    denied = harness.run_case({**verified_case, "verified_party_id": None,
                               "document_alternatives_exhausted": True})
    assert denied["case_tool_calls"] == 0 and denied["harness_status"] == "identity_required"


def test_adapter_excess_fields_do_not_enter_model(monkeypatch, verified_case):
    original = harness.tools.get_claim_for_action
    monkeypatch.setattr(harness.tools, "get_claim_for_action", lambda *args: {
        **original(*args), "net_pay": "999.00", "ssn": "private",
    })
    original_composer = llm.compose_case_answer
    def compose(question, intent, evidence, *args):
        claim = evidence["get_claim_for_action"]
        assert "net_pay" not in claim and "ssn" not in claim
        return original_composer(question, intent, evidence, *args)
    monkeypatch.setattr(llm, "compose_case_answer", compose)
    assert harness.run_case(verified_case)["harness_status"] == "completed"

"""The model selects capabilities, not a fallible ordered tool program."""
from claim_agent.llm import client as llm
from claim_agent.llm.client import plan_case_with_llm
from claim_agent.llm.schemas import CaseReadOptions


def test_followup_topic_always_has_its_tool(monkeypatch):
    monkeypatch.setattr(llm, "structured_call", lambda schema, *args: CaseReadOptions(
        decision="execute", document_guidance=True, followup_topic="missing_required_material_alternatives"))
    plan = plan_case_with_llm("What if I cannot get the documents?", "document_submission", {
        "get_claim_for_action", "get_document_guidance_for_claim", "get_claim_followup_guidance"})
    assert plan.tools == ["get_document_guidance_for_claim", "get_claim_followup_guidance"]
    assert plan.followup_topic == "missing_required_material_alternatives"


def test_plain_status_needs_no_optional_reads(monkeypatch):
    monkeypatch.setattr(llm, "structured_call", lambda schema, *args: CaseReadOptions(
        decision="execute", document_guidance=False, followup_topic=None))
    plan = plan_case_with_llm("What is my status?", "status_inquiry", {"get_claim_for_action"})
    assert plan.tools == [] and plan.followup_topic is None


def test_stop_decision_does_not_execute_unused_capabilities(monkeypatch):
    monkeypatch.setattr(llm, "structured_call", lambda schema, *args: CaseReadOptions(
        decision="clarify", document_guidance=True, followup_topic="submission_method"))
    plan = plan_case_with_llm("What should I do?", "document_submission", {"get_claim_for_action"})
    assert plan.decision == "clarify" and not plan.tools and plan.followup_topic is None


def test_optional_reads_outside_the_action_allowlist_are_not_requested(monkeypatch):
    monkeypatch.setattr(llm, "structured_call", lambda schema, *args: CaseReadOptions(
        decision="execute", document_guidance=True, followup_topic="submission_timing"))
    plan = plan_case_with_llm("What is the appeal deadline?", "appeal_deadline", {"get_claim_for_action"})
    assert plan.decision == "execute" and plan.tools == [] and plan.followup_topic is None

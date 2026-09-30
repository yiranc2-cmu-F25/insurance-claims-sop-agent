import re
import os

# Set before graph modules are imported: never open the developer's persistent DB.
os.environ["MEMORY_BACKEND"] = "memory"

import pytest

from claim_agent.llm.schemas import TurnExtraction
from claim_agent.llm.schemas import AnswerReview, CasePlan, GroundedAnswer
from claim_agent.guardrails.schemas import SecurityAssessment


def _fake_llm_extraction(text: str, **kwargs) -> TurnExtraction:
    """Test-only model stub; production intent decisions come from the LLM."""
    lower = text.lower()
    intent = "unknown"

    if any(value in lower for value in ["human representative", "live agent", "talk to a human"]):
        intent = "representative_request"
    elif "update my claim" in lower:
        intent = "claim_update"
    elif "upload" in lower or "attach" in lower:
        intent = "document_upload"
    elif "appeal deadline" in lower:
        intent = "appeal_deadline"
    elif "what documents" in lower or "which documents" in lower:
        intent = "document_submission"
    elif "next step" in lower:
        intent = "next_steps"
    elif "denial reason" in lower or "why was" in lower:
        intent = "denial_question"
    elif "status" in lower:
        intent = "status_inquiry"
    elif "claim" in lower:
        intent = "general_claim_question"

    case_type = "healthcare" if any(value in lower for value in ["healthcare", "medical", "hospital"]) else None
    status = "denied" if any(value in lower for value in ["denied", "denial", "rejected"]) else None
    month = "January" if "january" in lower else None
    dob = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    last4 = re.search(r"last four is (\d{4})", lower)
    policy = re.search(r"POL-\d+", text)

    return TurnExtraction(
        name="Margaret Chen" if "Margaret Chen" in text else None,
        dob=dob.group(0) if dob else None,
        id_last4=last4.group(1) if last4 else None,
        policy_number=policy.group(0) if policy else None,
        intent=intent,
        scope="out_of_scope" if "what is rl" in lower else "in_scope",
        emotion="frustrated" if "ridiculous" in lower else "neutral",
        case_type=case_type,
        status=status,
        month=month,
    )


@pytest.fixture(autouse=True)
def test_llm(monkeypatch):
    # No test may accidentally use a developer's real token.
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("DEMO_EMAIL_SCENARIO", "default")
    monkeypatch.setattr("claim_agent.llm.client.get_llm", lambda: None)
    monkeypatch.setattr("claim_agent.workflow.gates.assess_security", lambda *a, **kw: SecurityAssessment(
        risk="low", category="normal_customer_request", reason="Simulated ordinary request.",
        declared_role="unknown", clarification_topic="none",
    ))
    monkeypatch.setattr("claim_agent.workflow.intake.extract_turn_with_llm", _fake_llm_extraction)
    monkeypatch.setattr("claim_agent.llm.client.plan_case_with_llm", _fake_plan)
    monkeypatch.setattr("claim_agent.llm.client.compose_case_answer", _fake_answer)
    monkeypatch.setattr("claim_agent.llm.client.review_case_answer", lambda *args: AnswerReview(
        supported_by_evidence=True, answers_question=True, no_unauthorized_actions=True,
    ))


def _fake_plan(question, intent, allowed):
    selected = ["get_claim_for_action"]
    if intent in {"document_submission", "next_steps"} and "get_document_guidance_for_claim" in allowed:
        selected.append("get_document_guidance_for_claim")
    if intent == "payment_question":
        selected.append("get_claim_field_definitions")
    return CasePlan(decision="execute", tools=selected, followup_topic=None)


def _fake_answer(question, intent, evidence, *args):
    claim = evidence["get_claim_for_action"]
    text = f"Claim {claim['case_id']} is {claim['status']}. "
    if intent == "document_submission":
        text += "The requested documents are: " + ", ".join(claim.get("documents_needed", []))
    elif intent == "appeal_deadline":
        text += claim.get("appeal_deadline", "No deadline is recorded.")
    elif intent == "denial_question":
        text += claim.get("denial_reason", "No denial reason is recorded.")
    else:
        text += claim.get("summary", "")
    return GroundedAnswer(answer=text, sources=["get_claim_for_action"])

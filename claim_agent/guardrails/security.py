"""Semantic risk assessment; access permissions remain deterministic and separate."""

from ..llm.client import structured_call
from ..llm.prompts import ASSESS_SECURITY, REVIEW_IDENTITY_INPUT
from .schemas import IdentityInputReview, SecurityAssessment


CLARIFICATION_QUESTIONS = {
    "ownership": "Is this request about your own policy, or are you acting on behalf of a policyholder?",
    "requested_access": "What protected information are you asking to access, and on whose behalf?",
}
MAX_CLARIFICATION_ATTEMPTS = 2


def assess_security(text: str, *, context=None) -> SecurityAssessment:
    """No keyword decisions or default-low fallback; model failures propagate."""
    context = context or {}
    # Do not send customer records, stored PII, claim details or transcripts.
    role = context.get("caller_role", "unknown")
    pending = bool(context.get("clarification_pending"))
    topic = context.get("clarification_topic", "ownership" if pending else "none")
    safe_context = {
        "phase": context.get("phase", "VERIFY_ID"),
        "caller_role": role if role in {"policyholder", "delegate"} else "unknown",
        "identity_verified": bool(context.get("identity_verified")),
        "clarification_pending": pending,
        # Reconstruct only our approved question, never copy arbitrary history.
        "last_clarification_question": CLARIFICATION_QUESTIONS.get(topic, "") if pending else "",
    }
    result = structured_call(SecurityAssessment, ASSESS_SECURITY, {
        "message": text, "context": safe_context,
    })
    if result.risk == "medium":
        # A bounded semantic second check, never a default-low/keyword fallback.
        # High-risk and provider failures cannot enter this recovery path.
        review = structured_call(IdentityInputReview, REVIEW_IDENTITY_INPUT, {"message": text})
        if review.identity_input_only and review.source == text:
            return SecurityAssessment(
                risk="low", category="normal_customer_request",
                reason="The message only supplies identity data; formatting and verification remain required.",
                declared_role=result.declared_role, clarification_topic="none",
            )
    return result


def security_allows_actions(state) -> bool:
    """Require an explicit successful assessment, never a missing or stale default."""
    return state.get("security_status") == "cleared" and state.get("security_risk") == "low"

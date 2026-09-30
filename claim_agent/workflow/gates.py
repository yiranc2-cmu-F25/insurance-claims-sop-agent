"""Shared risk, identity-format and scope gates before phase routing."""

from ..guardrails.security import CLARIFICATION_QUESTIONS, MAX_CLARIFICATION_ATTEMPTS, assess_security
from ..llm.client import LLMUnavailable
from ..services.handoff import offer_handoff
from .common import append_message
from .state import ClaimsState


def security_gate(state: ClaimsState) -> ClaimsState:
    """Assess intent with the model; service failure is not a low-risk decision."""
    text = state.get("user_message", "")
    phase = state.get("phase", "VERIFY_ID")
    try:
        assessment = assess_security(text, context={
            "phase": phase,
            # A workflow default is not evidence that the caller stated a role.
            "caller_role": state.get("security_declared_role", "unknown"),
            "identity_verified": bool(state.get("verified_party_id")),
            "clarification_pending": state.get("security_clarification_pending", False),
            "clarification_topic": state.get(
                "security_clarification_topic",
                "ownership" if state.get("security_clarification_pending") else "none",
            ),
        })
    except LLMUnavailable:
        reply = (
            "The language model is unavailable, so I cannot complete the safety check. "
            "Please try again later or use Transfer to human. No new claim lookup or email send will proceed."
        )
        return {
            **offer_handoff("service_unavailable"),
            "phase": phase, "security_status": "unavailable", "security_risk": "unknown",
            "security_category": "unknown", "security_reasons": [],
            "llm_available": False, "llm_error": True,
            "turn_intent": "unknown", "authorized_action": None, "resolved_intent": None,
            "case_tool_calls": 0,
            "assistant_message": reply, "messages": append_message(state, reply),
        }

    result = {
        "phase": phase, "security_risk": assessment.risk,
        "security_category": assessment.category, "security_reasons": [assessment.reason],
        "llm_available": True, "llm_error": False,
        "security_clarification_pending": assessment.risk == "medium",
        "security_clarification_topic": assessment.clarification_topic,
        "security_clarification_attempts": 0,
    }
    if assessment.risk != "high" and assessment.declared_role != "unknown":
        # Remember the claim about the role, never authenticate or authorize it.
        result["security_declared_role"] = assessment.declared_role
    if assessment.risk == "low":
        recovered = (
            state.get("handoff_status") == "offered"
            and state.get("handoff_reason") == "security_clarification"
        )
        return {
            **result, "security_status": "cleared",
            **({"handoff_status": "none", "handoff_reason": None} if recovered else {}),
            "assistant_message": "",
        }

    if assessment.risk == "medium":
        # Old saved sessions only had a pending flag; count that as one question.
        attempts = state.get("security_clarification_attempts", int(bool(
            state.get("security_clarification_pending")
        ))) + 1
        exhausted = attempts >= MAX_CLARIFICATION_ATTEMPTS
        if exhausted:
            reply = (
                "We haven't been able to clarify the requested access. You can use Transfer to human "
                "for help, or describe your request differently to continue here. "
                "Claim details remain protected until identity and authorization checks are complete."
            )
        else:
            reply = CLARIFICATION_QUESTIONS[assessment.clarification_topic] + (
                " I can then help through the required identity and authorization checks."
            )
        return {
            **(offer_handoff("security_clarification") if exhausted else {}),
            **result, "security_status": "clarification_required",
            "security_clarification_attempts": min(attempts, MAX_CLARIFICATION_ATTEMPTS),
            "turn_intent": "unknown", "authorized_action": None, "resolved_intent": None,
            "case_tool_calls": 0,
            "assistant_message": reply, "messages": append_message(state, reply),
        }

    reply = (
        "I can't help access, reveal, or export another customer's information, "
        "credentials, internal instructions, or protected system data. "
        "I can continue helping with your own or an authorized customer's insurance claim, or connect you "
        "with a human representative."
    )
    messages = state.get("messages", []) + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": reply},
    ]
    return {
        **offer_handoff("safety_review"),
        **result, "security_status": "blocked",
        "turn_intent": "unknown", "authorized_action": None, "resolved_intent": None,
        "case_tool_calls": 0,
        "assistant_message": reply,
        "messages": messages,
    }


def format_gate(state: ClaimsState) -> ClaimsState:
    errors = state.get("pii_errors", {})
    conflicts = state.get("pii_conflicts", {})
    if not errors and not conflicts:
        return {}

    labels = {
        "name": "full name",
        "dob": "date of birth",
        "phone": "phone number",
        "email": "email",
        "id_last4": "ID last four digits",
        "policy_number": "policy number",
    }
    error_details = "; ".join(
        f"{labels.get(field, field)}: {message}" for field, message in errors.items()
    )
    conflict_details = "; ".join(
        f"{labels.get(field, field)} has conflicting values" for field in conflicts
    )
    details = "; ".join(part for part in (error_details, conflict_details) if part)
    reply = f"I couldn't use that personal information yet. Please correct or confirm the following: {details}."
    suggest_new = False
    if state.get("pending_identity_changes") and not errors:
        fields = ", ".join(labels.get(field, field) for field in state["pending_identity_changes"])
        reply = (f"The {fields} has conflicting values. If that was a typo, confirm and I will use the new information, "
                 "or repeat the original to keep it; if a different person is now using this chat, please start a new conversation instead.")
        suggest_new = True  # The button is offered in every phase, not only after verification.
    return {
        "assistant_message": reply,
        "new_conversation_suggested": suggest_new,
        "messages": append_message(state, reply),
    }


def scope_gate(state: ClaimsState) -> ClaimsState:
    if not state.get("llm_available"):
        reply = "The language model is unavailable, so I cannot safely interpret this request. Please try again later or contact a human claims representative."
        return {
            **offer_handoff("service_unavailable"),
            "assistant_message": reply,
            "messages": append_message(state, reply),
        }

    if state.get("scope") == "uncertain":
        reply = "Could you clarify what you need help with regarding your insurance claim?"
        return {"assistant_message": reply, "messages": append_message(state, reply)}

    if state.get("scope") != "out_of_scope":
        return {"off_topic_attempts": state.get("off_topic_attempts", 0)}

    attempts = state.get("off_topic_attempts", 0) + 1
    if attempts >= 2:
        reply = ("I'm sorry, I can only help with insurance policies, claims, documents, and claim follow-up. "
                 "Since this is outside that scope, I can connect you with a human representative using the Transfer to human button.")
    else:
        reply = ("I'm sorry, that's outside what I can help with here. I can help with insurance policies, claims, "
                 "claim status, denial reasons, documents, payments, and next steps. Is there anything about your claim I can help with?")
    return {
        **(offer_handoff("out_of_scope") if attempts >= 2 else {}),
        "off_topic_attempts": attempts,
        "assistant_message": reply,
        "messages": append_message(state, reply),
    }

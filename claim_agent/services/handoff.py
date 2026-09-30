"""Session-scoped mock human handoff, independent of the business phase."""
import re
import secrets
from datetime import datetime, timezone

from ..guardrails.policy import DISALLOWED_INTENTS
from .request_queue import mark_awaiting_caller
from .verification_session import authentication_is_current


REASONS = {
    "verification_locked": "Repeated identity mismatches require a cooldown or human assistance.",
    "customer_request": "You asked to speak with a human representative.",
    "emotional_support": "A human representative can give you more support with this conversation.",
    "verification_refused": "We could not continue identity verification with your current choices.",
    "out_of_scope": "Your request needs assistance outside this automated claims service.",
    "unsupported_request": "This request needs a human representative's assistance.",
    "service_unavailable": "The automated service is currently unavailable.",
    "tool_failure": "We could not complete the claim lookup.",
    "safety_review": "This request needs additional review before we can continue.",
    "security_clarification": "We could not clarify the requested access; a human can help with the required checks.",
    "email_failure": "We could not complete the email-summary step.",
    "delegate_authorization": "The requested access needs representative authorization review.",
    "claim_not_found": "No accessible claim was found for this request.",
    "documents_exhausted": "The required documents and available substitutes need a manual review.",
}

# Soft offers that later progress (a verification, a validated answer) retires.
RECOVERABLE_OFFERS = {
    "emotional_support", "verification_refused", "verification_locked", "out_of_scope",
    "claim_not_found", "service_unavailable", "tool_failure", "safety_review",
    "unsupported_request", "delegate_authorization",
}

INTENT_LABELS = {
    "status_inquiry": "Check claim status",
    "denial_question": "Understand the denial reason",
    "document_submission": "Find required documents or submission guidance",
    "appeal_deadline": "Check the recorded appeal deadline",
    "next_steps": "Understand the next steps",
    "general_claim_question": "Discuss a claim",
    "payment_question": "Understand recorded claim payment amounts",
    "claim_update": "Request a claim update",
    "document_upload": "Request document upload assistance",
    "new_claim": "File a new claim",
    "representative_request": "Speak with a human representative",
}

PAUSED_REPLY = (
    "Your simulated transfer request has been created. The bot is paused; no real "
    "representative is connected in this demo. Choose Return to bot to continue."
)


def offer_handoff(reason):
    if reason not in REASONS:
        raise ValueError("Unknown handoff reason")
    return {"handoff_status": "offered", "handoff_reason": reason}


def emotional_escalation_due(state):
    """Persuade first: escalate on sustained distress, never on a single complaint."""
    distress = state.get("distress_turns", 0)
    return distress >= 3 or (bool(state.get("needs_human_support")) and distress >= 2)


def emotional_escalation(state):
    return offer_handoff("emotional_support") if emotional_escalation_due(state) else {}


def clear_recoverable_offer(state):
    """Workflow progress retires a stale soft offer; a requested transfer is never touched."""
    if state.get("handoff_status") == "offered" and state.get("handoff_reason") in RECOVERABLE_OFFERS:
        return {"handoff_status": "none", "handoff_reason": None}
    return {}


def pause_gate(state):
    """Also protects direct graph invocation, not just the HTTP endpoint."""
    if state.get("handoff_status") != "requested":
        return {"assistant_message": ""}
    return {
        "user_message": "", "assistant_message": PAUSED_REPLY,
        "llm_error": False, "case_tool_calls": 0,
        "authorized_action": None, "resolved_intent": None,
    }


def handoff_gate(state):
    """Honor human requests before any phase-specific identity/claim action.

    Emotional distress never pauses the workflow here: the phase reply
    acknowledges it, explains the step, and offers a transfer once
    emotional_escalation_due() says persuasion has run its course.
    """
    if state.get("turn_intent") == "representative_request":
        reason = "customer_request"
        reply = (
            "You can use the Transfer to human button below. You do not need to "
            "complete identity verification to request help; claim details remain "
            "protected until verification is complete."
        )
    elif state.get("turn_intent") in DISALLOWED_INTENTS:
        reason = "unsupported_request"
        reply = "This request needs a human claims representative. You can use Transfer to human below."
    else:
        return {}
    # A request that cannot be served here must not sit in front of the caller's next question.
    queue = [mark_awaiting_caller(item) if item.get("intent") in DISALLOWED_INTENTS else item
             for item in state.get("pending_requests", [])]
    return {
        **offer_handoff(reason), "assistant_message": reply, "pending_requests": queue,
        "authorization_denied": reason == "unsupported_request",
        "messages": state.get("messages", []) + [{"role": "assistant", "content": reply}],
    }


def _redact(text, pii):
    # The summary never copies the transcript. Strip known PII defensively
    # from the already-validated assistant answers it may include.
    for value in sorted((str(v) for v in pii.values() if v), key=len, reverse=True):
        text = re.sub(re.escape(value), "[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", "[redacted]", text)
    return text


def build_handoff_summary(state):
    verified = authentication_is_current(state) and len(set(
        state.get("verification_matches", [])
    ) & {"name", "dob", "phone", "email", "id_last4"}) >= 3
    summary = {
        "verification": "verified" if verified else "required",
        "phase": state.get("phase", "VERIFY_ID"),
        "customer_request": INTENT_LABELS.get(state.get("requested_intent"), "Needs customer-service assistance"),
        "reason": REASONS.get(state.get("handoff_reason"), REASONS["unsupported_request"]),
        "completed_steps": ["Identity verification completed"] if verified else [],
        "discussed_items": [],
    }
    # No raw utterance, collected identity values, or caller-supplied claim
    # hints are forwarded. Unverified sessions never include claim details.
    if verified:
        summary["discussed_items"] = [
            _redact(answer, state.get("collected_pii", {}))[:2000]
            for answer in state.get("discussed_answers", [])[-10:]
        ]
        if summary["discussed_items"]:
            summary["completed_steps"].append("Claim responses checked against tool evidence")
    return summary


def request_handoff(state):
    """Called under the API's session lock, making repeat clicks idempotent."""
    if state.get("handoff_status") == "requested":
        return {}
    if state.get("handoff_status") != "offered":
        raise ValueError("No handoff has been offered")
    return {
        "handoff_status": "requested",
        "handoff_request_id": "DEMO-" + secrets.token_hex(8),
        "handoff_created_at": datetime.now(timezone.utc).isoformat(),
        "handoff_summary": build_handoff_summary(state),
        "llm_error": False,
        "case_tool_calls": 0,
        "authorized_action": None,
        "resolved_intent": None,
        "assistant_message": PAUSED_REPLY,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": PAUSED_REPLY}],
    }


def resume_bot(state):
    if state.get("handoff_status") != "requested":
        return {}
    reply = "The simulated transfer request is cancelled. You are back with the bot. "
    if state.get("phase", "VERIFY_ID") == "VERIFY_ID":
        reply += "Identity verification is still required before I can disclose claim details."
    elif state.get("phase") == "POST_PROCESS" and state.get("email_offer_pending"):
        reply += 'Use the "Yes, send summary" or "No, skip" button for the email summary, or ask another claim question.'
    else:
        reply += "You can continue your insurance question."
    return {
        "handoff_status": "none", "handoff_reason": None,
        "handoff_request_id": None, "handoff_created_at": None,
        "handoff_summary": None, "assistant_message": reply,
        "messages": state.get("messages", []) + [{"role": "assistant", "content": reply}],
    }


def public_handoff(state):
    status = state.get("handoff_status", "none")
    return {
        "status": status, "reason": state.get("handoff_reason"),
        "reason_text": REASONS.get(state.get("handoff_reason"), ""),
        "request_id": state.get("handoff_request_id") if status == "requested" else None,
        "created_at": state.get("handoff_created_at") if status == "requested" else None,
        "summary": state.get("handoff_summary") if status == "requested" else None,
        "simulated": True,
    }

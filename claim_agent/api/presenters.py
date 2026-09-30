"""Public response projection; internal identity and offer payloads stay private."""

from ..llm.client import get_llm_status
from ..services.handoff import public_handoff
from ..services.email_followup import public_email_offer, public_email_delivery
from ..services.verification_session import expire_identity, verification_is_locked, public_verification


def conversation_payload(state):
    state = {**state, **expire_identity(state)}
    return {
        "verification": public_verification(state),
        "verification_locked": verification_is_locked(state),
        "pending_questions": len(state.get("pending_requests", [])),
        "reply": state.get("assistant_message", ""),
        "phase": state.get("phase", "VERIFY_ID"),
        "verified": bool(state.get("verified_party_id")),
        "caller_role": state.get("caller_role", "policyholder"),
        "claim_id": state.get("selected_claim_id"),
        "intent": state.get("resolved_intent") or state.get("requested_intent"),
        "authorized_action": state.get("authorized_action"),
        "authorization_denied": bool(state.get("authorization_denied")),
        "security_status": state.get("security_status", "pending"),
        "email_consent": state.get("email_consent"),
        "email_offer": public_email_offer(state),
        "email_delivery": public_email_delivery(state),
        "harness_status": state.get("harness_status"),
        "case_tool_calls": state.get("case_tool_calls", 0),
        "llm_status": "unavailable" if state.get("llm_error") else get_llm_status(),
        "llm_available": (
            bool(state["llm_available"])
            if "llm_available" in state
            else get_llm_status() == "available"
        ),
        "handoff": public_handoff(state),
        "new_conversation_suggested": bool(state.get("new_conversation_suggested")),
    }

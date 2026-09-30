"""RESOLVE_INTENT: select an owned case and authorize a bounded action."""

from ..guardrails.policy import ALLOWED_ACTIONS, DISALLOWED_INTENTS, DISALLOWED_LABELS
from ..guardrails.access import has_access
from ..services.conversation_reply import compose_reply
from ..services.handoff import emotional_escalation, offer_handoff
from ..services.request_queue import report_head
from ..tools import select_claim, get_claims_for_party
from .common import append_message
from .state import ClaimsState


def resolve_node(state: ClaimsState) -> ClaimsState:
    if not has_access(state):
        reply = "Please verify your identity before accessing claim information."
        return {"phase": "VERIFY_ID", "verified_party_id": None, "selected_claim_id": None,
                "assistant_message": reply, "messages": append_message(state, reply)}
    requested_intent = state.get("requested_intent", "unknown")
    if requested_intent == "representative_request":
        reply = "I can connect you with a human claims representative."
        return {**offer_handoff("customer_request"), "assistant_message": reply, "messages": append_message(state, reply)}

    if requested_intent in DISALLOWED_INTENTS:
        action = DISALLOWED_INTENTS[requested_intent]
        reply = (
            f"I can't handle {DISALLOWED_LABELS.get(action, action.replace('_', ' '))} in this self-service chat. "
            "I can connect you with a human claims representative."
        )
        return {
            **offer_handoff("unsupported_request"),
            **report_head(state),
            "authorization_denied": True,
            "assistant_message": reply,
            "messages": append_message(state, reply),
        }

    if requested_intent == "unknown":
        menu = "I can help with claim status, denial reasons, required documents, appeal deadlines, or next steps. Which would you like to know?"
        reply = (
            "The language model is unavailable, so I cannot safely interpret this request. "
            "Please try again later or contact a human claims representative."
            if not state.get("llm_available")
            else compose_reply(state, menu)
        )
        return {
            "phase": "RESOLVE_INTENT",
            "assistant_message": reply,
            "messages": append_message(state, reply),
        }

    action = ALLOWED_ACTIONS.get(requested_intent)
    if not action or not has_access(state, action):
        reply = "Your authorization does not cover this request. You can use Transfer to human for help."
        return {**offer_handoff("delegate_authorization"), **report_head(state), "authorization_denied": True,
                "selected_claim_id": None, "assistant_message": reply, "messages": append_message(state, reply)}
    allowed_ids = [c["case_id"] for c in get_claims_for_party(state["verified_party_id"])
                   if has_access(state, action, c["case_id"])]
    claim, candidates = select_claim(
        state["verified_party_id"],
        state.get("intent_hint", {}),
        verified_party_id=state["verified_party_id"],
        allowed_claim_ids=allowed_ids,
    )
    if claim:
        return {
            "selected_claim_id": claim["case_id"],
            "resolved_intent": requested_intent,
            "phase": "RESOLVE_INTENT",
        }

    if len(candidates) > 1:
        options = ", ".join(f"{c['case_id']} ({c.get('created_at')})" for c in candidates[:4])
        reply = f"I found multiple claims that may match: {options}. Which claim would you like to discuss?"
    else:
        reply = (
            "I couldn't find an accessible claim matching those details. Please check the claim number or describe a different claim."
            if allowed_ids else "I couldn't find any claims available to you for this request. You can use Transfer to human for help."
        )
    # The head item now waits for the caller; a new question may supersede it, and an
    # explicit claim number that matched nothing is not carried into that question.
    return {
        **(offer_handoff("claim_not_found") if not allowed_ids else emotional_escalation(state)),
        **report_head(state),
        "intent_hint": {k: v for k, v in state.get("intent_hint", {}).items() if k != "case_id"},
        "selected_claim_id": None, "resolved_intent": None, "authorized_action": None,
        "phase": "RESOLVE_INTENT",
        "assistant_message": reply,
        "messages": append_message(state, reply),
    }


def authorization_node(state: ClaimsState) -> ClaimsState:
    intent = state.get("resolved_intent") or state.get("requested_intent", "unknown")
    action = ALLOWED_ACTIONS.get(intent)

    if not action or not has_access(state, action, state.get("selected_claim_id")) or not state.get("llm_available"):
        reply = "I can connect you with a human claims representative to handle that request."
        return {
            **offer_handoff("unsupported_request"),
            **report_head(state),
            "authorization_denied": True,
            "assistant_message": reply,
            "messages": append_message(state, reply),
        }

    return {
        "authorized_action": action,
        "authorization_denied": False,
        "phase": "PROCESS_CASE",
    }

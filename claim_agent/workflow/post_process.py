"""POST_PROCESS: handle follow-up questions; email decisions use API buttons."""

from ..guardrails.policy import ALLOWED_ACTIONS, DISALLOWED_INTENTS
from ..services.conversation_reply import compose_reply
from ..services.email_followup import EMAIL_PROMPT
from .common import append_message
from .state import ClaimsState


def _is_follow_up_request(state: ClaimsState) -> bool:
    return (bool(state.get("turn_case_hint")) or state.get("turn_has_requests")
            or (state.get("queue_continue") and bool(state.get("pending_requests")))) or state.get("turn_intent", "unknown") in (
        set(ALLOWED_ACTIONS) | set(DISALLOWED_INTENTS) | {"representative_request"}
    )


def post_process_node(state: ClaimsState) -> ClaimsState:
    if not state.get("llm_available") or not state.get("verified_party_id"):
        reply = "I cannot send an email until your identity and request have been confirmed."
        return {"assistant_message": reply, "messages": append_message(state, reply)}
    # Chat can reopen a claim question, but can never send or skip email.
    if _is_follow_up_request(state):
        return {
            "phase": "RESOLVE_INTENT",
            "authorized_action": None,
            "resolved_intent": None,
            "email_offer_pending": False,
            "email_offer": None,
            "email_result": None,
            "email_consent": None,
            "case_closed": False,
            "assistant_message": "",
        }

    if not state.get("email_offer_pending"):
        instruction = (
            "Demo: email approval is still pending. No real email was sent. You can keep asking claim questions."
            if (state.get("email_delivery") or {}).get("status") == "pending"
            else "The email choice is already complete. You can ask another claim question."
        )
    else:
        instruction = EMAIL_PROMPT
    # A thank-you or a process question deserves an answer; the instruction always follows it.
    answer = compose_reply(state, "")
    reply = f"{answer}\n\n{instruction}" if answer else instruction
    return {"assistant_message": reply, "messages": append_message(state, reply)}

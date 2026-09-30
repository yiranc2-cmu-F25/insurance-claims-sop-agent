"""Natural replies for in-scope non-claim turns. Facts come from state; no claim data is available."""
import re

from ..llm import client as llm
from .memory_policy import redact_text


MAX_REPLY_CHARS = 700
# No other claim/policy identifiers, amounts, contact details, or statements of a claim outcome.
FORBIDDEN = re.compile(
    r"CL-\d|POL-|\$|\d{2,}|@|\b(?:was|is|has been|were|got|been) (?:denied|approved|rejected|closed|paid|settled)\b",
    re.I,
)


def conversation_facts(state):
    return {
        "caller_message": redact_text(state.get("user_message", ""), state)[:600],
        "emotion": state.get("emotion", "neutral"),
        "phase": state.get("phase", "VERIFY_ID"),
        "identity_verified": bool(state.get("verified_party_id")),
        "caller_role": state.get("caller_role", "policyholder"),
        "current_claim_id": state.get("selected_claim_id") or state.get("discussed_claim_id"),
        "email_offer_pending": bool(state.get("email_offer_pending")),
    }


def acceptable(reply, claim_id):
    if not reply or len(reply) > MAX_REPLY_CHARS:
        return False
    return not FORBIDDEN.search(reply.replace(claim_id, "") if claim_id else reply)


def compose_reply(state, fallback):
    """Model wording for a conversational turn, or the fixed fallback when it is off-script or unavailable."""
    if state.get("dialogue_act") == "email_reply" or state.get("scope") != "in_scope":
        return fallback
    facts = conversation_facts(state)
    try:
        reply = str(llm.compose_conversation_reply(facts).reply).strip()
    except Exception:
        return fallback
    return reply if acceptable(reply, facts["current_claim_id"]) else fallback

"""Natural phrasing for VERIFY_ID replies. Code decides; the model only words it."""
import re

from ..llm import client as llm
from .handoff import INTENT_LABELS
from .memory_policy import redact_text


FIELD_LABELS = {
    "name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "last four digits of the ID on your policy (SSN or national ID)",
}
FIELD_KEYWORDS = {
    "name": ("name",),
    "dob": ("date of birth", "birth date", "birthday", "birth"),
    "phone": ("phone",),
    "email": ("email", "e-mail"),
    "id_last4": ("last four", "last 4", "four digits", "last-four"),
}
MAX_REPLY_CHARS = 900
# No claim/policy identifiers, amounts, dates, contact details or completion claims.
FORBIDDEN = re.compile(
    r"CL-\d|POL-|\$|\d{2,}|@|\b(?:you(?:'re| are)(?: now)? verified|verification (?:is )?(?:now )?complete)\b",
    re.I,
)
DELEGATE_NOTE = (
    "The caller is a delegate: they must use their own identity details, not the "
    "policyholder's, and must also give the policyholder's full name or policy "
    "number so the authorization on file can be checked."
)


def verification_facts(state, *, remaining, provided, available, attempt_failed, refusals, delegate, offer_human):
    """Labels, counts and flags only; never identity values, claim data or history."""
    intent = state.get("requested_intent", "unknown")
    return {
        "caller_message": redact_text(state.get("user_message", ""), state)[:600],
        "emotion": state.get("emotion", "neutral"),
        "consecutive_distressed_turns": state.get("distress_turns", 0),
        "refusal_count": refusals,
        "remaining_fields": remaining,
        "received_fields": [FIELD_LABELS[field] for field in provided],
        "accepted_fields": [FIELD_LABELS[field] for field in available],
        "details_did_not_match": remaining == 0,
        "attempt_failed": bool(attempt_failed),
        "remembered_request": INTENT_LABELS.get(intent) if intent != "representative_request" else None,
        "caller_role": "delegate" if delegate else "policyholder",
        "delegate_note": DELEGATE_NOTE if delegate else None,
        "identity_expired": bool(state.get("identity_expired")),
        "offer_human": bool(offer_human),
    }


def acceptable(reply, available):
    """Mechanical guard: no protected data, and the caller must be offered a way forward."""
    if not reply or len(reply) > MAX_REPLY_CHARS or FORBIDDEN.search(reply):
        return False
    lower = reply.lower()
    return not available or any(keyword in lower for field in available for keyword in FIELD_KEYWORDS[field])


def compose_reply(facts, available, fallback):
    """Return the model's wording, or the fixed template when it is unavailable or off-script."""
    try:
        reply = str(llm.compose_verification_reply(facts).reply).strip()
    except Exception:
        return fallback
    return reply if acceptable(reply, available) else fallback

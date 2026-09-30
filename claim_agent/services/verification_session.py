"""Server-enforced authentication lifetime and per-session verification cooldown."""
import os
from time import time
from dotenv import load_dotenv

load_dotenv()


def _seconds(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(name + " must be positive")
    return value


IDLE_SECONDS = _seconds("VERIFICATION_IDLE_SECONDS", 900)
MAX_AGE_SECONDS = _seconds("VERIFICATION_MAX_AGE_SECONDS", 3600)
MAX_FAILURES = _seconds("VERIFICATION_MAX_FAILURES", 5)
LOCK_SECONDS = _seconds("VERIFICATION_LOCK_SECONDS", 900)
WARNING_SECONDS = 120
EXPIRED_REPLY = "For your privacy, identity verification has expired. Please provide three identity details again; I have kept your pending questions."
LOCKED_REPLY = "There have been too many unsuccessful verification attempts. Please try again after the cooldown or use Transfer to human. Claim details remain protected."


def authentication_is_current(state):
    if not state.get("verified_party_id") or state.get("pending_identity_changes"):
        return False
    verified = state.get("verified_at")
    active = state.get("verified_last_active_at")
    if not isinstance(verified, (float, int)) or not isinstance(active, (float, int)):
        return False
    now = time()
    return (0 <= now - verified < MAX_AGE_SECONDS
            and verified <= active <= now and now - active < IDLE_SECONDS
            and not verification_is_locked(state))


def verification_is_locked(state):
    return state.get("verification_locked_until", 0) > time()


def public_verification(state):
    remaining = 0
    if authentication_is_current(state):
        remaining = max(0, min(state["verified_at"] + MAX_AGE_SECONDS,
                               state["verified_last_active_at"] + IDLE_SECONDS) - time())
    return {"remaining_seconds": remaining, "warning_seconds": WARNING_SECONDS,
            "expired": bool(state.get("identity_expired")),
            "locked": verification_is_locked(state),
            "retry_after_seconds": max(0, state.get("verification_locked_until", 0) - time())}


def clear_identity_context():
    """Never clear the attempt counter here: changing roles cannot bypass cooldown."""
    return {
        "phase": "VERIFY_ID", "verified_party_id": None, "verified_caller_id": None,
        "verified_at": None, "verified_last_active_at": None, "verification_matches": [],
        "selected_claim_id": None, "resolved_intent": None, "authorized_action": None,
        "authorization_denied": False, "grounded_claim": None, "discussed_answers": [], "discussed_claim_id": None,
        "email_offer": None, "email_offer_pending": False, "email_result": None,
        "email_consent": None, "email_delivery": None, "handoff_summary": None,
        "case_closed": False,
        "case_memories": {}, "messages": [],
    }


def expire_identity(state):
    if state.get("verified_party_id") and not authentication_is_current(state):
        return {
            **clear_identity_context(), "collected_pii": {}, "pending_identity_changes": {},
            "pii_errors": {}, "pii_conflicts": {}, "identity_expired": True,
            "assistant_message": EXPIRED_REPLY,
            "security_status": "pending", "security_risk": "unknown",
        }
    return {}


def session_guard(state):
    update = expire_identity(state)
    current = {**state, **update}
    if verification_is_locked(current):
        return {**update, "phase": "VERIFY_ID", "handoff_status": "offered",
                "handoff_reason": "verification_locked", "assistant_message": LOCKED_REPLY,
                "llm_error": False, "case_tool_calls": 0,
                "messages": state.get("messages", []) + [{"role": "assistant", "content": LOCKED_REPLY}]}
    if current.get("verification_locked_until"):
        update.update(verification_locked_until=0, verification_failed_attempts=0,
                      collected_pii={}, pending_identity_changes={}, pii_conflicts={})
    if authentication_is_current(current):
        update["verified_last_active_at"] = time()
    return {**update, "assistant_message": ""}

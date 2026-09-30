"""Bounded conversation memory and minimal, redacted model context."""
import os
import re
from time import time

from dotenv import load_dotenv
from ..guardrails.input_guard import detect_sensitive_overcollection, sanitize_user_text

load_dotenv()


def positive_setting(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(name + " must be positive")
    return value


MAX_MESSAGES = positive_setting("MEMORY_MAX_MESSAGES", 20)
MAX_CHECKPOINTS = positive_setting("MEMORY_MAX_CHECKPOINTS", 30)
RETENTION_SECONDS = positive_setting("MEMORY_RETENTION_SECONDS", 86400)
CONTEXT_MESSAGES = 6
MAX_CASES = 5
MAX_CASE_ENTRIES = 5


def bounded_messages(previous, incoming):
    """Nodes supply a replacement list, not a delta; preserve server timestamps."""
    now = time()
    return [{**m, "at": m.get("at", now)} for m in (incoming or [])
            if now - m.get("at", now) < RETENTION_SECONDS][-MAX_MESSAGES:]


def redact_text(text, state, extra=None):
    text = sanitize_user_text(text)
    if detect_sensitive_overcollection(text):
        return "[sensitive input withheld]"
    values = {**state.get("collected_pii", {}), **(extra or {})}
    candidates = list(values.values()) + list(state.get("pending_identity_changes", {}).values())
    candidates += list(state.get("represented_customer", {}).values())
    # Type labels aren't personal identifiers.
    for value in sorted({str(v) for v in candidates if v and str(v) not in {"ssn_last4", "national_id_last4"}}, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", "[identity redacted]", text, flags=re.I)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", "[email redacted]", text)
    text = re.sub(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)",
                  lambda m: "[number redacted]" if len(re.sub(r"\D", "", m[0])) >= 10 else m[0], text)
    text = re.sub(r"\bPOL-\d+\b", "[policy redacted]", text, flags=re.I)
    text = re.sub(r"(?i)((?:last\s*(?:four|4))\D{0,12})\d{4}\b", r"\1[redacted]", text)
    return text


def recent_context(state):
    # Imported lazily: access checks themselves use authentication services.
    from ..guardrails.access import has_access
    from ..guardrails.policy import ALLOWED_ACTIONS
    from ..tools import get_claims_for_party
    permitted = has_access(state)
    owned = {c["case_id"] for c in get_claims_for_party(state["verified_party_id"])} if permitted else set()
    result = []
    for message in bounded_messages([], state.get("messages", []))[-CONTEXT_MESSAGES:]:
        if message.get("role") not in {"user", "assistant"}:
            continue
        claim_ids = re.findall(r"\bCL-\d+\b", message.get("content", ""), re.I)
        if message["role"] == "assistant":
            if state.get("verified_party_id") and not permitted:
                continue
            if not claim_ids and state.get("caller_role") == "delegate" and state.get("verified_party_id"):
                claim_ids = list(state.get("case_memories", {}))
            # Conservative: a restricted delegate must not recover formerly broader answers.
            if not all(cid.upper() in owned and has_access(state, action, cid.upper()) for cid in claim_ids
                       for action in set(ALLOWED_ACTIONS.values())):
                continue
        result.append({"role": message["role"], "content": redact_text(message["content"], state)[:800]})
    return result

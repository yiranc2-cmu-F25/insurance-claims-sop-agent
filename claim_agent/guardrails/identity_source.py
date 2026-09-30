"""Bind identity updates to literal evidence in the current message."""
import re

from .normalization import normalize_date


IDENTITY_FIELDS = (
    "name", "dob", "phone", "email", "id_last4", "policy_number",
    "represented_name", "represented_policy_number",
)
REDACTION_MARKERS = (
    "[identity redacted]", "[email redacted]", "[number redacted]",
    "[policy redacted]", "[redacted]", "[sensitive input withheld]",
)


def current_source(message, value):
    """A normalized guess, historical value, or substring of a longer token is not evidence."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if any(marker in value.casefold() for marker in REDACTION_MARKERS):
        return False
    return re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", message) is not None


def source_checked_identity(message, extracted, *, pending_fields=()):
    """Only deterministic formatting follows the source check; never invent PII."""
    values = {}
    for field in IDENTITY_FIELDS:
        raw = getattr(extracted, field)
        if current_source(message, raw):
            raw = raw.strip()
            # Claim references are not personal identity or policy identifiers.
            if re.fullmatch(r"CL-\d+", raw, re.I):
                continue
            # Invalid volunteered dates still reach the normal format validator.
            values[field] = (normalize_date(raw) or raw) if field == "dob" else raw
    if extracted.id_type and current_source(message, extracted.id_type.source):
        values["id_type"] = extracted.id_type.kind
    if extracted.caller_role and current_source(message, extracted.caller_role.source):
        values["caller_role"] = extracted.caller_role.role
    if (pending_fields and extracted.identity_correction
            and current_source(message, extracted.identity_correction.source)):
        values["identity_correction"] = extracted.identity_correction.decision
    return values

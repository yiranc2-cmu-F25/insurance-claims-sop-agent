"""Bind new requests/search hints to this turn; history only resolves references."""
import re

from .identity_source import IDENTITY_FIELDS, current_source
from .policy import ALLOWED_ACTIONS, DISALLOWED_INTENTS
from ..services.memory_policy import redact_text


HINT_FIELDS = ("case_id", "case_type", "status", "month", "year")


def checked_hints(message, item, identity):
    # Remove current identity spans before accepting years or other search hints.
    searchable = message
    for field in IDENTITY_FIELDS:
        value = getattr(identity, field)
        if value and not re.fullmatch(r"CL-\d+", value, re.I) and current_source(message, value):
            searchable = searchable.replace(value, "[identity]")
    result = {}
    for field in HINT_FIELDS:
        value = getattr(item, field)
        if not value:
            continue
        value = value.strip()
        if field in {"case_id", "year"}:
            pattern = r"CL-\d+" if field == "case_id" else r"\d{4}"
            # Typed literals are not guessed, normalized, or recovered from history.
            if re.fullmatch(pattern, value, re.I) and current_source(searchable.casefold(), value.casefold()):
                result[field] = value.upper() if field == "case_id" else value
        else:
            source = getattr(item.hint_sources, field) or value
            if current_source(searchable, source):
                result[field] = value
    return result


def source_checked_business(message, understanding, identity):
    data = understanding.model_dump()
    # This is an LLM-selected dialogue act, not a keyword/yes-no heuristic.
    if understanding.dialogue_act in {"email_reply", "identity_reply"}:
        data.update(intent="unknown", requests=[], request_mode="append", new_case=False,
                    document_alternatives_exhausted=None)
        data.update({field: None for field in HINT_FIELDS})
        return data

    data.update({field: None for field in HINT_FIELDS})
    data.update(checked_hints(message, understanding, identity))
    questions = []
    raw_identity = {field: getattr(identity, field) for field in IDENTITY_FIELDS
                    if getattr(identity, field) and not re.fullmatch(r"CL-\d+", getattr(identity, field), re.I)}
    for question in understanding.requests:
        source = question.source or question.question
        if not current_source(message, source):
            continue
        hints = checked_hints(message, question, identity)
        # Persist the actual user request, not a model-invented rewrite/year/ID.
        context = message if len(understanding.requests) == 1 and len(message) <= 800 else source
        questions.append({**question.model_dump(), **{field: None for field in HINT_FIELDS},
                          **hints, "source": None,
                          "question": redact_text(context, {}, raw_identity)[:800]})
    data["requests"] = questions
    if questions:
        data["intent"] = questions[0]["intent"]
    elif understanding.intent in set(ALLOWED_ACTIONS) | set(DISALLOWED_INTENTS):
        # A stale top-level label must not synthesize a request from a greeting.
        data["intent"] = "unknown"
    return data


def upgrade_legacy_hints(hints):
    """Discard unproven legacy years and incorrectly typed identifiers on first use."""
    clean = {key: value for key, value in hints.items() if key != "year"}
    if clean.get("case_id") and not re.fullmatch(r"CL-\d+", str(clean["case_id"]), re.I):
        clean.pop("case_id")
    return clean

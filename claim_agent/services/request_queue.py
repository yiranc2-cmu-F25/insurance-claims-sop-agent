"""Bounded question backlog; LLM interprets questions, code owns ordering/completion."""
from uuid import uuid4

from ..guardrails.normalization import normalize_hints
from ..guardrails.policy import ALLOWED_ACTIONS, DISALLOWED_INTENTS

MAX_PENDING = 10
MAX_PER_TURN = 3
HINT_FIELDS = ("case_id", "case_type", "status", "month", "year")


TRANSIENT_FAILURES = {"llm_unavailable", "tool_failed"}


def mark_awaiting_caller(item):
    """The caller was asked to clarify this item, or told it cannot proceed as asked."""
    return {**item, "awaiting_caller": True}


def mark_retry(item):
    """A transient failure: keep the item, but never ahead of the caller's next question."""
    return {**item, "retry": True}


def report_head(state):
    queue = list(state.get("pending_requests", []))
    if queue:
        queue[0] = mark_awaiting_caller(queue[0])
    return {"pending_requests": queue}


def collect_requests(state, extracted, hints, text, reset=False):
    queue = [] if reset else list(state.get("pending_requests", []))
    if extracted.request_mode in {"replace", "cancel"}:
        queue = []
    if extracted.request_mode == "cancel":
        return queue
    incoming = []
    for question in extracted.requests:
        own_hints = {key: getattr(question, key) for key in HINT_FIELDS if getattr(question, key)}
        # Explicit per-question hints take precedence over shared turn hints.
        question_hints = dict(hints)
        if own_hints.get("case_id") or any(k in hints and hints[k] != v for k, v in own_hints.items()):
            question_hints = {}
        question_hints.update(own_hints)
        incoming.append({"id": uuid4().hex, "intent": question.intent,
                         "question": question.question, "hints": normalize_hints(question_hints)})
    # The same wording extracted twice is one question; keep the specific intent over the fallback.
    specific = {item["question"].strip().casefold() for item in incoming if item["intent"] != "general_claim_question"}
    incoming = [item for item in incoming
                if item["intent"] != "general_claim_question" or item["question"].strip().casefold() not in specific]
    if not incoming and extracted.intent in set(ALLOWED_ACTIONS) | set(DISALLOWED_INTENTS):
        incoming = [{"id": uuid4().hex, "intent": extracted.intent, "question": text, "hints": hints}]
    # An item the caller was already asked about is superseded by any new question
    # or an explicit "continue"; it must never block the rest of the backlog.
    if incoming or extracted.request_mode == "continue":
        queue = [item for item in queue if not item.get("awaiting_caller")]
    # Items that failed transiently are retried after the new questions, not before.
    if incoming:
        retries = [{k: v for k, v in item.items() if k != "retry"} for item in queue if item.get("retry")]
        queue = [item for item in queue if not item.get("retry")]
        incoming = incoming + retries
    # Case-only clarification refreshes the waiting head, never creates a new request.
    if queue and not incoming and hints:
        previous_hints = queue[0]["hints"]
        queue = [
            {**{k: v for k, v in item.items() if k != "awaiting_caller"}, "hints": hints}
            if item["hints"] == previous_hints else item
            for item in queue
        ]
    for item in incoming:
        if not any((q["intent"], q["question"], q["hints"]) == (item["intent"], item["question"], item["hints"]) for q in queue):
            queue.append(item)
    if len(queue) > MAX_PENDING:
        raise ValueError("Too many pending questions")
    return queue


def activate_request(item):
    return {"requested_intent": item["intent"], "requested_question": item["question"],
            "intent_hint": item["hints"], "resolved_intent": None, "authorized_action": None,
            "selected_claim_id": None, "assistant_message": "", "phase": "RESOLVE_INTENT"}

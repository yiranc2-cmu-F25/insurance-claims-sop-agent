"""PROCESS_CASE: execute the harness and offer follow-up only on success."""

from ..services.case_harness import run_case
from ..services.handoff import clear_recoverable_offer, emotional_escalation, offer_handoff
from ..services.email_followup import EMAIL_PROMPT, new_email_offer
from ..services.request_queue import MAX_PER_TURN, activate_request, mark_awaiting_caller
from ..services.verification_session import expire_identity
from ..services.case_memory import prune_cases, remember_answer
from .resolve_intent import resolve_node, authorization_node
from .common import append_message
from .state import ClaimsState


def process_node(state: ClaimsState) -> ClaimsState:
    queue = list(state.get("pending_requests", []))
    working = dict(state)
    replies = []
    answers = (list(state.get("discussed_answers", []))
               if state.get("discussed_claim_id") == working.get("selected_claim_id") else [])
    finished = list(state.get("completed_requests", []))
    case_memories = prune_cases(state.get("case_memories", {}))
    audit = list(state.get("audit_events", []))
    calls = 0
    handoff = {}
    completed = False
    result = {}
    # Questions the caller was just asked to clarify keep their place in front of
    # the queue, but they must not block the other questions from the same turn.
    awaiting = []
    # Each sub-question gets independent case ownership, action and answer checks.
    for index in range(MAX_PER_TURN):
        if index:
            if not queue:
                break
            previous_claim = working.get("selected_claim_id")
            working.update(activate_request(queue[0]))
            working.update(resolve_node(working))
            if not working.get("assistant_message"):
                working.update(authorization_node(working))
            if working.get("assistant_message"):
                replies.append(working["assistant_message"])
                handoff = {k: working[k] for k in ("handoff_status", "handoff_reason") if k in working}
                completed = False
                denied = bool(working.get("authorization_denied"))
                result = {"harness_status": "authorization_denied" if denied else "clarification_needed"}
                queue[0] = mark_awaiting_caller(queue[0])
                if denied:
                    break
                awaiting.append(queue.pop(0))
                continue
            if working.get("selected_claim_id") != previous_claim:
                answers = []  # An email offer always summarizes its single bound claim.
        result = run_case(working)
        calls += result["case_tool_calls"]
        audit.extend(result["audit_events"])
        replies.append(result["reply"])
        completed = result["harness_status"] == "completed"
        if not completed:
            if result["harness_status"] == "clarification_needed":
                if queue:
                    awaiting.append(mark_awaiting_caller(queue.pop(0)))
                continue
            reason = {"llm_unavailable": "service_unavailable", "tool_failed": "tool_failure",
                      "human_required": "unsupported_request"}.get(result["harness_status"], "safety_review")
            handoff = offer_handoff(reason)
            break
        answers.append(result["reply"])
        case_memories = remember_answer(case_memories, working, result)
        if queue:
            item = queue.pop(0)
            finished.append({"id": item["id"], "intent": item["intent"], "claim_id": working["selected_claim_id"]})
        if result.get("documents_exhausted"):
            handoff = offer_handoff("documents_exhausted")
            replies.append("Since the documents and substitutes are unavailable, you can use Transfer to human for a manual review.")
        if not queue:
            break
    queue = awaiting + queue

    expired = expire_identity(state)
    if expired:
        return {**expired, "pending_requests": state.get("pending_requests", []),
                "case_tool_calls": calls, "audit_events": audit[-200:]}
    all_done = completed and not queue
    waiting = [item for item in queue if not item.get("awaiting_caller")]
    if all_done:
        replies.append(EMAIL_PROMPT)
    elif waiting:
        replies.append(f"I have {len(waiting)} question(s) still pending. You can clarify, say continue, or ask to replace or cancel them.")
    if not handoff:
        # A delivered answer is progress: it retires a stale offer and resets the distress
        # clock. Only an undelivered turn under sustained distress earns a human option.
        handoff = clear_recoverable_offer(state) if completed else emotional_escalation(state)
    reply = "\n\n".join(replies)
    next_request = activate_request(queue[0]) if queue else {}
    unavailable = result.get("harness_status") == "llm_unavailable"
    return {
        **{k: working.get(k) for k in ("selected_claim_id", "requested_intent", "requested_question", "intent_hint", "authorization_denied")},
        **next_request,
        **handoff,
        "grounded_claim": result.get("grounded_claim"),
        "phase": "POST_PROCESS" if all_done else "RESOLVE_INTENT",
        "harness_status": "completed" if all_done else ("pending_questions" if completed else result.get("harness_status", "clarification_needed")),
        "case_tool_calls": calls,
        "audit_events": audit[-200:],
        "llm_available": not unavailable,
        "llm_error": unavailable,
        "authorized_action": working.get("authorized_action") if all_done else None,
        "resolved_intent": working.get("resolved_intent") if all_done else None,
        "pending_requests": queue,
        "completed_requests": finished[-50:],
        "case_memories": case_memories,
        **({"distress_turns": 0} if completed else {}),
        "email_consent": None,
        "email_offer_pending": all_done,
        "email_offer": new_email_offer(working, answers, result.get("grounded_claim")) if all_done else None,
        "email_result": None,
        "case_closed": False,
        "discussed_answers": answers,
        "discussed_claim_id": working.get("selected_claim_id"),
        "assistant_message": reply,
        "messages": append_message(state, reply),
    }

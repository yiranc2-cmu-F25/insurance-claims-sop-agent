"""Understand each new turn and retain information for later phases."""

from ..llm.client import LLMUnavailable, extract_turn_with_llm
from ..guardrails.normalization import normalize_hints, normalize_pii, validate_pii_formats
from ..guardrails.business_source import upgrade_legacy_hints
from ..services.handoff import offer_handoff
from ..services.verification_session import SWITCH_REFUSED, clear_identity_context
from ..tools.identity import resolve_represented_customer
from ..services.identity_corrections import apply_identity_input
from ..services.request_queue import collect_requests
from ..services.memory_policy import recent_context, redact_text
from ..services.case_memory import accessible_case_notes
from .common import append_message
from .state import ClaimsState


def _refuse_other_person(state, extracted):
    """Drop access and point to a new conversation; the bound caller may verify again."""
    return {
        **clear_identity_context(), "collected_pii": {}, "pending_identity_changes": {}, "pii_conflicts": {},
        "pii_errors": {}, "represented_customer": {}, "pending_requests": [], "requested_intent": "unknown",
        "requested_question": "", "intent_hint": {}, "llm_available": True, "llm_error": False,
        "turn_intent": "unknown", "emotion": extracted.emotion, "distress_turns": 0,
        "security_declared_role": state.get("caller_role", "policyholder"), "new_conversation_suggested": True,
        "assistant_message": SWITCH_REFUSED, "messages": [{"role": "assistant", "content": SWITCH_REFUSED}],
    }


def capture_turn(state: ClaimsState) -> ClaimsState:
    if state.get("hint_schema_version", 0) < 1:
        state = {**state, "intent_hint": upgrade_legacy_hints(state.get("intent_hint", {})),
                 "pending_requests": [{**q, "hints": upgrade_legacy_hints(q.get("hints", {}))}
                                      for q in state.get("pending_requests", [])]}
    text = state.get("user_message", "")
    phase = state.get("phase", "VERIFY_ID")
    declared_role = state.get("security_declared_role", "unknown")
    role_context = declared_role if declared_role in {"policyholder", "delegate"} else state.get("caller_role", "policyholder")
    context_messages = recent_context(state)
    try:
        extracted = extract_turn_with_llm(text, context={
            "phase": phase,
            "email_offer_pending": bool(state.get("email_offer_pending")),
            "remembered_intent": state.get("requested_intent", "unknown"),
            "previous_emotion": state.get("emotion", "neutral"),
            "refusal_attempts": state.get("refusal_attempts", 0),
            "distress_turns": state.get("distress_turns", 0),
            "claim_hints": state.get("intent_hint", {}),
            "caller_role": role_context,
            "represented_customer": {field: "[identity redacted]" for field in state.get("represented_customer", {})},
            "document_alternatives_exhausted": state.get("document_alternatives_exhausted", False),
            "pending_identity_fields": list(state.get("pending_identity_changes", {})),
            "pending_requests": [{"intent": q["intent"], "question": redact_text(q["question"], state)} for q in state.get("pending_requests", [])],
            "recent_messages": context_messages,
            "case_notes": accessible_case_notes(state),
            "last_assistant_message": next((m["content"] for m in reversed(context_messages)
                                             if m["role"] == "assistant"), ""),
        })
        if extracted is None:
            raise LLMUnavailable()
    except LLMUnavailable:
        reply = "The language model is unavailable. Please try again later or contact a human claims representative."
        return {
            **offer_handoff("service_unavailable"),
            "phase": phase, "llm_available": False, "llm_error": True,
            "turn_intent": "unknown",
            "assistant_message": reply, "messages": append_message(state, reply),
        }

    # Only the source-checked identity extractor can change the active role.
    role = extracted.caller_role or state.get("caller_role", "policyholder")
    role_changed = role != state.get("caller_role", "policyholder")
    target = {} if role_changed else dict(state.get("represented_customer", {}))
    for field in ("name", "policy_number"):
        value = getattr(extracted, "represented_" + field)
        if value:
            target[field] = value
    target = normalize_pii(target)
    previous_target = state.get("represented_customer", {})
    if state.get("session_identity"):
        # The conversation is bound to its first verified caller. A new role or a different
        # customer means another person is typing: refuse, drop access, ask for a new conversation.
        new_party = resolve_represented_customer(target) if target and target != previous_target else None
        if role_changed or (new_party and new_party != state["session_identity"][1]):
            return _refuse_other_person(state, extracted)
        if target != previous_target:
            target = dict(previous_target)  # a re-mention of the same customer keeps the verified target
    identity_changed = role_changed or target != previous_target
    reset = {}
    if identity_changed:
        phase = "VERIFY_ID"
        reset = {
            "case_memories": {},
            "verified_party_id": None, "verified_caller_id": None,
            "verification_matches": [], "selected_claim_id": None,
            "verified_at": None, "verified_last_active_at": None,
            "authorized_action": None, "resolved_intent": None,
            "authorization_denied": False, "grounded_claim": None,
            "discussed_answers": [], "email_offer": None,
            "email_offer_pending": False, "email_result": None,
            "email_consent": None, "email_delivery": None,
            "discussed_claim_id": None, "distress_turns": 0,
        }
    turn_pii = {
        field: getattr(extracted, field)
        for field in ("name", "dob", "phone", "email", "id_last4", "id_type", "policy_number")
        if getattr(extracted, field)
    }
    incoming_hints = {
        field: getattr(extracted, field)
        for field in ("case_type", "status", "month", "year", "case_id")
        if getattr(extracted, field)
    }
    old_hints = state.get("intent_hint", {})
    changed_hint = any(old_hints.get(k) and old_hints[k].casefold() != str(v).casefold()
                       for k, v in incoming_hints.items())
    switching_customer = identity_changed and bool(
        state.get("verified_party_id") or state.get("represented_customer")
    )
    switching_case = switching_customer or extracted.new_case or changed_hint
    hints = {} if switching_case or incoming_hints.get("case_id") else dict(old_hints)
    hints.update(incoming_hints)
    if switching_case or (incoming_hints.get("case_id") and incoming_hints["case_id"].upper() != state.get("selected_claim_id")):
        reset.update({
            "selected_claim_id": None, "resolved_intent": None, "authorized_action": None,
            "grounded_claim": None, "discussed_answers": [], "email_offer": None,
            "email_offer_pending": False, "email_result": None, "email_consent": None,
        })
    # A previous caller's remembered request never carries over to a new caller.
    remembered_intent = "unknown" if switching_customer else state.get("requested_intent", "unknown")
    remembered_question = "" if switching_customer else state.get("requested_question", "")
    requested_intent = extracted.intent
    if requested_intent == "representative_request":
        requested_intent = remembered_intent
    # Remember an early request while identity/case clarification is completed.
    if requested_intent == "unknown" and (
        phase == "VERIFY_ID" or incoming_hints or extracted.new_case or (phase == "RESOLVE_INTENT" and turn_pii)
    ):
        requested_intent = remembered_intent

    pii_errors = validate_pii_formats(turn_pii)
    pii_errors.update({"represented_" + k: v for k, v in validate_pii_formats(target).items()})
    identity_update = apply_identity_input(state, turn_pii, pii_errors, extracted.identity_correction, role_changed)
    if state.get("session_identity") and identity_update.get("pending_identity_changes"):
        # After verification a conflicting identity field is not a typo to confirm: it is another person.
        return _refuse_other_person(state, extracted)
    if identity_update.get("phase"):
        phase = identity_update["phase"]
    try:
        queue = collect_requests(state, extracted, normalize_hints(hints), text, reset=switching_customer)
    except ValueError:
        reply = "There are too many pending questions. Please let me finish the existing ones or ask to replace them."
        return {**identity_update, "assistant_message": reply, "messages": append_message(state, reply)}
    if queue:
        requested_intent = queue[0]["intent"]
        hints = queue[0]["hints"]
    if extracted.request_mode == "cancel":
        requested_intent = "unknown"
        phase = "RESOLVE_INTENT" if state.get("verified_party_id") and not identity_update.get("phase") else "VERIFY_ID"
        reset.update(email_offer=None, email_offer_pending=False, email_result=None,
                     email_consent=None, authorized_action=None, resolved_intent=None)

    # Consecutive non-neutral turns drive the code-side escalation floor.
    distress_turns = state.get("distress_turns", 0) + 1 if extracted.emotion != "neutral" else 0

    return {
        **reset,
        **identity_update,
        "phase": phase,
        "caller_role": role,
        "security_declared_role": extracted.caller_role or declared_role,
        "represented_customer": target,
        "turn_case_hint": bool(incoming_hints) or extracted.new_case,
        "document_alternatives_exhausted": (
            extracted.document_alternatives_exhausted
            if extracted.document_alternatives_exhausted is not None
            else False if switching_case else state.get("document_alternatives_exhausted", False)
        ),
        "pii_errors": pii_errors,
        "intent_hint": normalize_hints(hints),
        "hint_schema_version": 1,
        "turn_intent": extracted.intent,
        "requested_intent": requested_intent,
        "requested_question": "" if extracted.request_mode == "cancel" else (queue[0]["question"] if queue else (text if extracted.intent not in {"unknown", "representative_request"} else remembered_question)),
        "pending_requests": queue,
        "queue_continue": extracted.request_mode == "continue",
        "turn_has_requests": bool(extracted.requests),
        "llm_available": True,
        "llm_error": False,
        "emotion": extracted.emotion,
        "distress_turns": distress_turns,
        "dialogue_act": extracted.dialogue_act,
        "needs_human_support": extracted.needs_human_support,
        "scope": extracted.scope,
        "assistant_message": "Your pending questions have been cleared. What would you like to discuss?" if extracted.request_mode == "cancel" else "",
        "messages": ([] if identity_changed or identity_update.get("phase") else state.get("messages", [])) + [{"role": "user", "content": redact_text(text, state, turn_pii)}] + ([
            {"role": "assistant", "content": "Your pending questions have been cleared. What would you like to discuss?"}
        ] if extracted.request_mode == "cancel" else []),
    }

"""VERIFY_ID: require three matching identity fields before claim access."""

from ..services.handoff import clear_recoverable_offer, emotional_escalation_due, offer_handoff
from ..services.verification_reply import compose_reply, verification_facts
from ..tools import verify_identity
from ..tools.identity import verify_delegate_identity, resolve_represented_customer, get_delegate_authorization
from ..services import verification_session as auth
from .common import append_message
from .state import ClaimsState


IDENTITY_FIELDS = ["name", "dob", "phone", "email", "id_last4"]
IDENTITY_LABELS = {
    "name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "last four digits of the ID on your policy (SSN or national ID)",
}
DELEGATE_NOTE = (
    " Please use your own identity details, not the policyholder's; "
    "I also need their full name or policy number to check your authorization."
)


def _template_reply(state, refusals, remaining, available):
    """Fixed wording, used when the phrasing model is unavailable or off-script."""
    if refusals >= 2:
        return (
            "I understand you do not want to repeat the information. I still cannot disclose claim details "
            "without matching at least three identity fields. I can transfer you to a human representative."
        )
    if remaining > 0:
        choices = ", ".join(IDENTITY_LABELS[field] for field in available)
        plural = "detail" if remaining == 1 else "details"
        empathy = "I understand this is frustrating. " if state.get("emotion") in {"angry", "frustrated", "anxious"} else ""
        return (
            f"{empathy}I still need {remaining} more identity "
            f"{plural} before I can continue. Please provide any of these: {choices}."
        )
    return (
        "I couldn't verify those details yet. Please check the information and "
        "provide another matching identity field, such as your phone, email, or the last four digits "
        "of the ID on your policy (SSN or national ID); the ID type must match the one registered."
    )


def verify_node(state: ClaimsState) -> ClaimsState:
    if auth.verification_is_locked(state):
        return {**offer_handoff("verification_locked"), "phase": "VERIFY_ID",
                "assistant_message": auth.LOCKED_REPLY, "messages": append_message(state, auth.LOCKED_REPLY)}
    if state.get("pending_identity_changes"):
        reply = "Please confirm or cancel the proposed identity correction before verification continues."
        return {"phase": "VERIFY_ID", "assistant_message": reply, "messages": append_message(state, reply)}
    delegate = state.get("caller_role") == "delegate"
    verifier = verify_delegate_identity if delegate else verify_identity
    caller_id, matches = verifier(state.get("collected_pii", {}))
    party_id = caller_id
    if caller_id and delegate:
        target = state.get("represented_customer", {})
        party_id = resolve_represented_customer(target)
        if not party_id or not get_delegate_authorization(caller_id, party_id):
            missing_target = not target
            reply = (
                "I verified your identity. Please provide the policyholder's full name or policy number so I can check your authorization."
                if missing_target else
                "I could not confirm active authorization for that customer. A family relationship alone does not permit access. You can use Transfer to human for help."
            )
            return {
                **({} if missing_target else offer_handoff("delegate_authorization")),
                "phase": "VERIFY_ID", "verified_party_id": None,
                "verified_caller_id": caller_id, "verification_matches": matches,
                "assistant_message": reply, "messages": append_message(state, reply),
            }
    if party_id:
        now = auth.time()
        # A confirmed identity change that lands on a different customer is a new caller:
        # the previous caller's questions and hints must not be answered for them.
        switched = bool(state.get("prior_party_id")) and state["prior_party_id"] != party_id
        return {
            **clear_recoverable_offer(state),
            **({"pending_requests": [], "requested_intent": "unknown", "requested_question": "", "intent_hint": {}} if switched else {}),
            "prior_party_id": None,
            "distress_turns": 0,
            "verified_at": now, "verified_last_active_at": now, "identity_expired": False,
            "verification_failed_attempts": 0, "verification_locked_until": 0,
            "verified_caller_id": caller_id,
            "verified_party_id": party_id,
            "verification_matches": matches,
            "phase": "RESOLVE_INTENT",
        }

    failures = state.get("verification_failed_attempts", 0)
    provided = [field for field in IDENTITY_FIELDS if state.get("collected_pii", {}).get(field)]
    # Incomplete answers, general questions and provider outages are not failures.
    attempt_failed = len(provided) >= 3 and bool(state.get("turn_identity_supplied")) and not caller_id
    if attempt_failed:
        failures += 1
    if failures >= auth.MAX_FAILURES:
        return {
            **auth.clear_identity_context(), **offer_handoff("verification_locked"),
            "verification_failed_attempts": failures,
            "verification_locked_until": auth.time() + auth.LOCK_SECONDS,
            "collected_pii": {}, "pending_identity_changes": {}, "pii_conflicts": {},
            "assistant_message": auth.LOCKED_REPLY, "messages": append_message(state, auth.LOCKED_REPLY),
        }
    refusals = state.get("refusal_attempts", 0) + (1 if state.get("emotion") == "refusing" else 0)
    remaining = max(0, 3 - len(provided))
    available = [field for field in IDENTITY_FIELDS if field not in provided]
    escalate = refusals >= 2 or emotional_escalation_due(state)

    # Code fixes every fact and the decision; the model only chooses the words.
    template = _template_reply(state, refusals, remaining, available)
    if delegate:
        template += DELEGATE_NOTE
    if state.get("identity_expired"):
        template = "For your privacy, please verify again; the previous verification expired. " + template
    facts = verification_facts(
        state, remaining=remaining, provided=provided, available=available,
        attempt_failed=attempt_failed, refusals=refusals, delegate=delegate, offer_human=escalate,
    )
    reply = compose_reply(facts, available, template)
    handoff = (
        offer_handoff("verification_refused") if refusals >= 2
        else offer_handoff("emotional_support") if emotional_escalation_due(state) else {}
    )
    return {
        "verification_failed_attempts": failures,
        "verified_party_id": None,
        **handoff,
        "phase": "VERIFY_ID",
        "verification_matches": matches,
        "refusal_attempts": refusals,
        "assistant_message": reply,
        "messages": append_message(state, reply),
    }

"""Historical notes are for understanding references, never fresh tool evidence."""
from time import time

from ..guardrails.access import has_access
from ..tools import get_claims_for_party
from .memory_policy import MAX_CASES, MAX_CASE_ENTRIES, RETENTION_SECONDS, redact_text


def identity_key(state):
    return (state.get("verified_party_id"), state.get("verified_caller_id"), state.get("caller_role", "policyholder"))


def prune_cases(cases):
    current = {cid: note for cid, note in cases.items()
               if time() - note.get("updated_at", 0) < RETENTION_SECONDS}
    return {cid: {**note, "entries": [e for e in note.get("entries", [])
                                      if time() - e.get("recorded_at", 0) < RETENTION_SECONDS][-MAX_CASE_ENTRIES:]}
            for cid, note in sorted(current.items(), key=lambda item: item[1]["updated_at"])[-MAX_CASES:]}


def remember_answer(cases, state, result):
    cases = prune_cases(cases)
    cid = state["selected_claim_id"]
    previous = cases.get(cid, {})
    entries = list(previous.get("entries", [])) if tuple(previous.get("identity", [])) == identity_key(state) else []
    # Replace the old response for this exact question; older notes remain explicitly historical.
    question = redact_text(state.get("requested_question", ""), state)[:800]
    entries = [e for e in entries if (e["intent"], e["question"]) != (state["resolved_intent"], question)]
    entries.append({"intent": state["resolved_intent"], "action": state["authorized_action"],
                    "question": question, "answer": redact_text(result["reply"], state)[:2000],
                    "source": "validated_tool_answer", "recorded_at": time()})
    cases[cid] = {"identity": list(identity_key(state)), "updated_at": time(), "entries": entries[-MAX_CASE_ENTRIES:]}
    return prune_cases(cases)


def accessible_case_notes(state):
    if not has_access(state):
        return []
    owned = {c["case_id"] for c in get_claims_for_party(state["verified_party_id"])}
    result = []
    for cid, note in prune_cases(state.get("case_memories", {})).items():
        if cid not in owned or tuple(note.get("identity", [])) != identity_key(state):
            continue
        entries = [e for e in note["entries"] if has_access(state, e["action"], cid)]
        if entries:
            result.append({"claim_id": cid, "historical_only": True, "entries": entries[-2:]})
    return result

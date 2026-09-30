"""Two-turn correction proposals; changing identity invalidates prior access."""
from ..guardrails.normalization import find_pii_conflicts, normalize_pii
from .verification_session import clear_identity_context


def apply_identity_input(state, incoming, errors, decision, role_changed=False):
    existing = {} if role_changed else dict(state.get("collected_pii", {}))
    pending = {} if role_changed else dict(state.get("pending_identity_changes", {}))
    usable = normalize_pii({k: v for k, v in incoming.items() if k not in errors})
    # Restating a value settles a pending change without a separate yes/no:
    # repeating the original keeps it, repeating the proposal adopts it.
    for key in list(pending):
        if usable.get(key) is not None and usable[key] == existing.get(key):
            pending.pop(key)
        elif usable.get(key) is not None and usable[key] == pending[key]:
            existing[key] = pending.pop(key)
    conflicts = find_pii_conflicts(existing, usable)
    if existing.get("id_type") and usable.get("id_type") and existing["id_type"] != usable["id_type"]:
        conflicts["id_type"] = "conflicting ID type"
    new_proposal = any(pending.get(k) != usable[k] for k in conflicts)
    reset = {}
    if pending and decision == "reject" and not new_proposal:
        pending = {}
    elif pending and decision == "confirm" and not new_proposal and not errors:
        existing.update(pending)
        pending = {}
        reset = clear_identity_context()
    else:
        pending.update({k: usable[k] for k in conflicts})
    for key, value in usable.items():
        if key not in conflicts and key not in pending:
            existing[key] = value
    if pending:
        reset = clear_identity_context()
    return {
        **reset, "collected_pii": normalize_pii(existing),
        "pending_identity_changes": pending,
        "pii_conflicts": {field: "awaiting correction confirmation" for field in pending},
        "turn_identity_supplied": bool(usable) or (decision == "confirm" and bool(state.get("pending_identity_changes"))),
    }

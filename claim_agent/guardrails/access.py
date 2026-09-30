"""Recheck delegated authority at selection, tool execution and email boundaries."""
from ..tools.identity import get_delegate_authorization, verify_delegate_identity
from ..services.verification_session import authentication_is_current


def has_access(state, action=None, claim_id=None):
    if not authentication_is_current(state) or len(set(state.get("verification_matches", [])) & {
        "name", "dob", "phone", "email", "id_last4"
    }) < 3:
        return False
    role = state.get("caller_role", "policyholder")
    if role == "policyholder":
        return True
    if role != "delegate":
        return False
    caller, _ = verify_delegate_identity(state.get("collected_pii", {}))
    if not caller or caller != state.get("verified_caller_id"):
        return False
    grant = get_delegate_authorization(caller, state["verified_party_id"])
    return bool(grant and (action is None or action in grant["actions"])
                and (claim_id is None or claim_id in grant["claim_ids"]))

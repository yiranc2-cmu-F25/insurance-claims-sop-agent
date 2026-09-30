"""Owned-claim selection and action-scoped field access."""

from calendar import month_name
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .fixtures import load_claims, load_claim_schema


def get_claims_for_party(party_id: str) -> List[Dict[str, Any]]:
    return [claim for claim in load_claims() if claim.get("party_id") == party_id]


def get_claim(party_id: str, claim_id: str) -> Optional[Dict[str, Any]]:
    for claim in get_claims_for_party(party_id):
        if claim.get("case_id") == claim_id:
            return claim
    return None


def select_claim(
    party_id: str,
    hint: Dict[str, str],
    *,
    verified_party_id: Optional[str] = None,
    allowed_claim_ids: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Select using remembered hints; return candidates when clarification is needed."""
    if verified_party_id != party_id:
        return None, []
    candidates = get_claims_for_party(party_id)
    if allowed_claim_ids is not None:
        candidates = [c for c in candidates if c["case_id"] in allowed_claim_ids]
    if hint.get("case_id"):
        candidates = [c for c in candidates if c["case_id"] == hint["case_id"].strip().upper()]
        return (candidates[0] if len(candidates) == 1 else None), candidates

    for key in ("case_type", "status"):
        value = hint.get(key)
        if value:
            candidates = [c for c in candidates if str(c.get(key, "")).lower() == value.lower()]

    month = hint.get("month")
    if month:
        month_number = next((str(i) for i in range(1, 13) if month_name[i].lower().startswith(month.lower()[:3])), None)
        if month_number:
            candidates = [c for c in candidates if c.get("created_at", "").split("-")[1] == month_number.zfill(2)]

    year = hint.get("year")
    if year:
        candidates = [c for c in candidates if c.get("created_at", "").startswith(year)]

    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


ACTION_FIELDS = {
    "read_claim_amounts": {
        "case_id", "case_type", "created_at", "status", "summary",
        "expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee",
    },
    "read_claim_status": {
        "case_id", "case_type", "created_at", "status", "summary"
    },
    "read_denial_reason": {
        "case_id", "case_type", "created_at", "status", "summary", "denial_reason",
        "documents_needed", "appeal_deadline", "appeal_deadline_passed", "as_of",
    },
    "read_document_guidance": {
        "case_id", "case_type", "created_at", "status", "summary", "documents_needed"
    },
    "read_appeal_deadline": {
        "case_id", "case_type", "created_at", "status", "summary", "appeal_deadline",
        "appeal_deadline_passed", "as_of",
    },
    "read_next_steps": {
        "case_id", "case_type", "created_at", "status", "summary", "documents_needed",
        "appeal_deadline", "appeal_deadline_passed", "as_of",
    },
    "read_claim_summary": {
        "case_id", "case_type", "created_at", "status", "summary"
    },
}


def current_date() -> date:
    """Injectable clock so deadline status is testable."""
    return date.today()


def get_claim_for_action(
    verified_party_id: str,
    claim_id: str,
    action: str,
) -> Optional[Dict[str, Any]]:
    """Return only fields authorized for the requested action."""
    claim = get_claim(verified_party_id, claim_id)
    allowed_fields = ACTION_FIELDS.get(action)
    if not claim or not allowed_fields:
        return None
    result = {key: value for key, value in claim.items() if key in allowed_fields}
    # A deadline is a fact relative to today; an expired one must never read as open.
    if result.get("appeal_deadline") and "appeal_deadline_passed" in allowed_fields:
        today = current_date()
        try:
            result["appeal_deadline_passed"] = date.fromisoformat(str(result["appeal_deadline"])) < today
            result["as_of"] = today.isoformat()
        except ValueError:
            pass
    return result


def get_claim_field_definition(field_name: str) -> Optional[Dict[str, Any]]:
    return load_claim_schema().get("field_descriptions", {}).get(field_name)


def get_claim_field_definitions(verified_party_id: str, claim_id: str) -> Optional[Dict[str, Any]]:
    """Return meanings only for amounts on this owned claim, excluding example amounts."""
    claim = get_claim_for_action(verified_party_id, claim_id, "read_claim_amounts")
    if not claim:
        return None
    fields = {}
    for field in ("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee"):
        definition = get_claim_field_definition(field)
        if field in claim and definition:
            fields[field] = {k: v for k, v in definition.items() if k in {"type", "description"}}
    return {"case_id": claim_id, "currency": "USD", "fields": fields,
            "limitations": "Expected amounts are not guarantees; net_fee is not a recorded patient responsibility amount."}

"""Policyholder lookup, representative lookup and identity matching."""

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ..guardrails.normalization import normalize_pii
from .fixtures import load_policyholders, load_representatives


def get_policyholder(party_id: str) -> Optional[Dict[str, Any]]:
    return next((p for p in load_policyholders() if p.get("party_id") == party_id), None)


def get_policyholder_by_policy(policy_number: str) -> Optional[Dict[str, Any]]:
    normalized = re.sub(r"[^A-Z0-9]", "", policy_number.upper())
    return next(
        (
            p
            for p in load_policyholders()
            if re.sub(r"[^A-Z0-9]", "", p.get("policy_number", "").upper()) == normalized
        ),
        None,
    )


def find_authorized_representative(rep_name: str, buyer_party_id: str) -> Optional[Dict[str, Any]]:
    """Return only the representative relationship for the verified customer."""
    for representative in load_representatives():
        if (
            representative.get("rep_name", "").lower() == rep_name.strip().lower()
            and representative.get("buyer_party_id") == buyer_party_id
        ):
            return representative
    return None


def _normal(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _phone(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def _field_matches(pii: Dict[str, str], person: Dict[str, Any]) -> List[str]:
    matches: List[str] = []

    name_values = [person.get("name", "")] + person.get("name_aliases", [])
    if pii.get("name") and any(_normal(pii["name"]) == _normal(v) for v in name_values):
        matches.append("name")

    if pii.get("dob") and pii["dob"] == person.get("dob"):
        matches.append("dob")

    phone_values = [person.get("phone", "")] + person.get("phone_aliases", [])
    if pii.get("phone") and any(_phone(pii["phone"]) == _phone(v) for v in phone_values):
        matches.append("phone")

    email_values = [person.get("email", "")] + person.get("email_aliases", [])
    if pii.get("email") and any(pii["email"].strip().casefold() == v.strip().casefold() for v in email_values):
        matches.append("email")

    if (pii.get("id_last4") and pii["id_last4"] == person.get("id_last4")
            and (not pii.get("id_type") or pii["id_type"] == person.get("id_type"))):
        matches.append("id_last4")

    return matches


def verify_identity(pii: Dict[str, str]) -> Tuple[Optional[str], List[str]]:
    """Return a party only when at least three PII fields match."""
    pii = normalize_pii(pii)
    best_party: Optional[str] = None
    best_matches: List[str] = []

    for person in load_policyholders():
        matches = _field_matches(pii, person)
        if len(matches) > len(best_matches):
            best_party = person["party_id"]
            best_matches = matches

    if len(best_matches) >= 3:
        candidate = next(
            (person for person in load_policyholders() if person.get("party_id") == best_party),
            None,
        )
        # A volunteered policy number is an additional consistency check. It
        # does not count toward the three PII requirement.
        if candidate and pii.get("policy_number"):
            supplied_policy = re.sub(r"[^A-Z0-9]", "", pii["policy_number"].upper())
            stored_policy = re.sub(r"[^A-Z0-9]", "", candidate.get("policy_number", "").upper())
            if supplied_policy != stored_policy:
                return None, best_matches
        return best_party, best_matches
    return None, best_matches


def verify_delegate_identity(pii):
    """Authenticate the actual caller, never using the policyholder's PII."""
    pii = normalize_pii(pii)
    candidates = {}
    best = []
    for rep in load_representatives():
        matches = _field_matches(pii, {**rep, "name": rep.get("rep_name")})
        if len(matches) > len(best):
            best = matches
        if rep.get("rep_id") and len(matches) >= 3:
            candidates[rep["rep_id"]] = matches
    if len(candidates) == 1:
        return next(iter(candidates.items()))
    return None, best


def get_delegate_authorization(rep_id, party_id, *, today=None):
    """A relationship alone grants nothing; dates, status and scope are required."""
    today = today or date.today()
    for rep in load_representatives():
        if rep.get("rep_id") != rep_id or rep.get("buyer_party_id") != party_id:
            continue
        grant = rep.get("authorization", {})
        try:
            valid = date.fromisoformat(grant["valid_from"]) <= today <= date.fromisoformat(grant["expires_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if (valid and grant.get("status") == "active"
                and isinstance(grant.get("actions"), list)
                and isinstance(grant.get("claim_ids"), list)):
            return grant
    return None


def resolve_represented_customer(target):
    """Require an unambiguous customer reference, not a guessed family link."""
    candidates = load_policyholders()
    if not target.get("name") and not target.get("policy_number"):
        return None
    if target.get("policy_number"):
        candidates = [p for p in candidates if _normal(p.get("policy_number")) == _normal(target["policy_number"])]
    if target.get("name"):
        candidates = [p for p in candidates if any(
            _normal(target["name"]) == _normal(n)
            for n in [p.get("name", "")] + p.get("name_aliases", [])
        )]
    return candidates[0]["party_id"] if len(candidates) == 1 else None

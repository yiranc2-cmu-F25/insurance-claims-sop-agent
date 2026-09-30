"""Claim-owned document requirements and follow-up guidance."""

from typing import Any, Dict, Optional

from .claims import get_claim
from .fixtures import load_document_guidance


def get_document_guidance_for_claim(
    verified_party_id: str,
    claim_id: str,
    document: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return claim-owned document guidance after ownership is checked."""
    claim = get_claim(verified_party_id, claim_id)
    if not claim:
        return None

    data = load_document_guidance()
    result: Dict[str, Any] = {
        "case_id": claim_id,
        "case_type": claim.get("case_type"),
        "default": data.get("default_guidance", {}).get("en", ""),
        "case_type_guidance": data.get("case_type_guidance", {})
        .get(claim.get("case_type"), {})
        .get("en", ""),
        "documents": {},
        "alternatives": {},
        "default_alternative": data.get("document_alternative_guidance", {}).get("default", {}).get("en", ""),
        "human_review": data.get("claim_followup_settings", {}).get("human_review_after_document_alternatives_exhausted", {}).get("en", ""),
    }

    requested = [document] if document else claim.get("documents_needed", [])
    for item in requested:
        key = item
        if key not in data.get("document_guidance", {}):
            key = next(
                (candidate for candidate in data.get("document_guidance", {}) if item.lower() in candidate.lower() or candidate.lower() in item.lower()),
                item,
            )
        result["documents"][item] = data.get("document_guidance", {}).get(key, {}).get("en", "")
        result["alternatives"][item] = data.get("document_alternative_guidance", {}).get(key, {}).get("en", "")
    return result


def get_claim_followup_guidance(
    verified_party_id: str,
    claim_id: str,
    intent: str,
    topic: str,
) -> Optional[str]:
    """Look up an LLM-selected topic; no interpretation of user keywords."""
    claim = get_claim(verified_party_id, claim_id)
    if not claim:
        return None

    data = load_document_guidance()
    documents = ", ".join(claim.get("documents_needed", []))
    average_time = data.get("claim_followup_settings", {}).get("average_processing_time_after_submission", {}).get("en", "")
    for rule in data.get("claim_followup_guidance", []):
        if rule.get("topic") != topic or intent not in rule.get("intent_hints", []):
            continue
        if rule.get("requires_documents") and not claim.get("documents_needed"):
            continue
        return rule.get("en", "").format(
            case_id=claim_id,
            documents=documents,
            average_processing_time_after_submission=average_time,
        )

    return None

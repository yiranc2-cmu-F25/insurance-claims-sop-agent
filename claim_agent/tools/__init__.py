"""Public tool interface; implementations are grouped by business capability."""

from .claims import (
    ACTION_FIELDS, get_claim, get_claim_field_definition, get_claim_field_definitions, get_claim_for_action,
    get_claims_for_party, select_claim,
)
from .documents import get_claim_followup_guidance, get_document_guidance_for_claim
from .email import get_consent_status, send_email_summary
from .fixtures import (
    load_claim_schema, load_claims, load_consent_scenarios, load_document_guidance,
    load_policyholders, load_representatives,
)
from .identity import (
    find_authorized_representative, get_policyholder, get_policyholder_by_policy,
    verify_identity,
)

__all__ = [
    "ACTION_FIELDS", "get_claim", "get_claim_field_definition", "get_claim_field_definitions", "get_claim_for_action",
    "get_claims_for_party", "select_claim", "get_claim_followup_guidance",
    "get_document_guidance_for_claim", "get_consent_status", "send_email_summary",
    "load_claim_schema", "load_claims", "load_consent_scenarios", "load_document_guidance",
    "load_policyholders", "load_representatives", "find_authorized_representative",
    "get_policyholder", "get_policyholder_by_policy", "verify_identity",
]

"""Mock email delivery adapter; consent and ownership checks remain mandatory."""

import os
from typing import Any, Dict, Optional

from .claims import get_claim
from .fixtures import load_consent_scenarios
from .identity import get_policyholder


def get_consent_status(scenario: str = "default", poll_index: int = 0) -> str:
    scenarios = load_consent_scenarios()
    sequence = scenarios.get(scenario, {}).get("status_sequence", [])
    if not sequence or poll_index < 0:
        return "unknown"
    return sequence[min(poll_index, len(sequence) - 1)]


def send_email_summary(
    verified_party_id: str,
    claim_id: str,
    summary: str,
    *,
    consent: bool,
    scenario: Optional[str] = None,
) -> Dict[str, Any]:
    """Mock email tool with explicit consent and fixture-backed delivery status."""
    if not consent:
        return {"status": "not_sent", "reason": "explicit consent required"}

    policyholder = get_policyholder(verified_party_id)
    claim = get_claim(verified_party_id, claim_id)
    if not policyholder or not claim or not policyholder.get("email"):
        return {"status": "failed", "reason": "verified email or owned claim not found"}

    scenario = scenario or os.getenv("DEMO_EMAIL_SCENARIO", "default")
    return {
        "status": get_consent_status(scenario, 0),
        "scenario": scenario,
        "recipient": policyholder["email"],
        "claim_id": claim_id,
        "summary": summary,
    }

"""Fixture storage adapter. Replace this layer when integrating real data."""

import json
from typing import Any, Dict, List

from ..paths import FIXTURES_DIR

FIXTURES = FIXTURES_DIR


def _load(name: str) -> Any:
    with (FIXTURES / name).open("r", encoding="utf-8") as file:
        return json.load(file)


def load_policyholders() -> List[Dict[str, Any]]:
    return _load("policyholders.json")


def load_claims() -> List[Dict[str, Any]]:
    return _load("claims.json")


def load_representatives() -> List[Dict[str, Any]]:
    return _load("representatives.json")


def load_consent_scenarios() -> Dict[str, Any]:
    return _load("consent_scenarios.json")


def load_claim_schema() -> Dict[str, Any]:
    return _load("claim_schema.json")


def load_document_guidance() -> Dict[str, Any]:
    return _load("required_document_guideline.json")

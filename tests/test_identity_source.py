"""Identity provenance checks and the authenticated document-follow-up regression."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from claim_agent.guardrails.identity_source import source_checked_identity
from claim_agent.guardrails.normalization import validate_pii_formats
from claim_agent.llm import client as llm
from claim_agent.llm.schemas import (
    ClaimQuestion, CorrectionStatement, IdentityExtraction, RoleStatement,
    TurnUnderstanding,
)
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


FOLLOWUP = "What documents do I need, and what if I cannot get them?"


@pytest.mark.parametrize("value", ["[identity redacted]", "[redacted]", "4472", "null"])
def test_unsourced_identity_is_not_a_customer_format_error(value):
    result = source_checked_identity(FOLLOWUP, IdentityExtraction(id_last4=value))
    assert result == {}
    assert validate_pii_formats(result) == {}


def test_valid_partial_identity_uses_the_literal_source_before_normalization():
    result = source_checked_identity(
        "My date of birth is March 15, 1985.",
        IdentityExtraction(dob="March 15, 1985", name="Margaret Chen", id_last4="4472"),
    )
    assert result == {"dob": "1985-03-15"}
    assert source_checked_identity(
        "My date of birth is March 15, 1985.", IdentityExtraction(dob="1985-03-15"),
    ) == {}


@pytest.mark.parametrize("dob", ["1985-99-99", "03/04/1985"])
def test_invalid_or_ambiguous_current_date_is_not_silently_repaired(dob):
    result = source_checked_identity("My DOB is " + dob, IdentityExtraction(dob=dob))
    assert result == {"dob": dob}
    assert "dob" in validate_pii_formats(result)


def test_wrong_length_is_preserved_and_cannot_be_clipped_to_four_digits():
    message = "My SSN last four are 44723."
    assert source_checked_identity(message, IdentityExtraction(id_last4="4472")) == {}
    raw = source_checked_identity(message, IdentityExtraction(id_last4="44723"))
    assert "id_last4" in validate_pii_formats(raw)


def test_role_and_correction_require_current_source_and_a_pending_correction():
    invented = IdentityExtraction(
        caller_role=RoleStatement(role="delegate", source="calling for my mother"),
        identity_correction=CorrectionStatement(decision="confirm", source="yes"),
    )
    assert source_checked_identity(FOLLOWUP, invented, pending_fields=["dob"]) == {}
    actual = IdentityExtraction(identity_correction=CorrectionStatement(decision="confirm", source="Yes"))
    assert source_checked_identity("Yes, use the new DOB.", actual) == {}
    assert source_checked_identity("Yes, use the new DOB.", actual, pending_fields=["dob"]) == {
        "identity_correction": "confirm",
    }


def test_delegate_identity_and_target_values_are_independently_source_checked():
    message = "I'm David Chen, calling for Margaret Chen, policy POL-9921."
    result = source_checked_identity(message, IdentityExtraction(
        name="David Chen", represented_name="Margaret Chen", represented_policy_number="POL-9921",
        caller_role=RoleStatement(role="delegate", source="calling for Margaret Chen"),
        dob="1985-03-15",
    ))
    assert result == {"name": "David Chen", "represented_name": "Margaret Chen",
                      "represented_policy_number": "POL-9921", "caller_role": "delegate"}


def test_business_output_has_no_identity_mutation_channel():
    for field in ("name", "id_last4", "caller_role", "represented_name", "identity_correction"):
        with pytest.raises(ValidationError):
            TurnUnderstanding(**{field: "invented"})


def test_identity_model_never_receives_history_stored_values_or_case_notes(monkeypatch):
    calls = []
    def structured(schema, instructions, payload):
        calls.append((schema, payload))
        return IdentityExtraction(id_last4="[identity redacted]") if schema is IdentityExtraction else TurnUnderstanding(
            intent="document_submission", scope="in_scope", requests=[
                ClaimQuestion(intent="document_submission", question=FOLLOWUP, source=FOLLOWUP),
            ],
        )
    monkeypatch.setattr(llm, "structured_call", structured)
    context = {"caller_role": "policyholder", "pending_identity_fields": ["dob"],
               "recent_messages": [{"content": "private-history"}],
               "case_notes": {"private-case": "private-details"},
               "collected_pii": {"name": "private-name"}}
    result = llm.extract_turn_with_llm(FOLLOWUP, context=context)
    by_schema = dict(calls)  # the two calls run concurrently; order is not defined
    assert set(by_schema) == {IdentityExtraction, TurnUnderstanding}
    assert by_schema[IdentityExtraction] == {"message": FOLLOWUP, "context": {
        "caller_role": "policyholder", "pending_identity_fields": ["dob"],
    }}
    assert "private-" not in str(by_schema[IdentityExtraction])
    assert by_schema[TurnUnderstanding]["context"] == context
    assert result.id_last4 is None and result.intent == "document_submission"


def test_authenticated_followup_does_not_overwrite_pii_or_reenter_verification(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    before = graph.invoke({"user_message": (
        "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
        "Why was my healthcare claim from January denied?"
    )}, config)
    assert before["verified_party_id"] == "P9"
    assert before["phase"] == "POST_PROCESS"
    def structured(schema, instructions, payload):
        if schema is IdentityExtraction:
            return IdentityExtraction(id_last4="[identity redacted]", name="A made-up name")
        return TurnUnderstanding(intent="document_submission", scope="in_scope", requests=[
            ClaimQuestion(intent="document_submission", question=FOLLOWUP),
        ])
    monkeypatch.setattr(llm, "structured_call", structured)
    monkeypatch.setattr(intake, "extract_turn_with_llm", llm.extract_turn_with_llm)
    after = graph.invoke({"user_message": FOLLOWUP}, config)
    assert after["collected_pii"] == before["collected_pii"]
    assert after["verified_party_id"] == "P9" and after["phase"] == "POST_PROCESS"
    assert not after["pii_errors"] and not after["pending_identity_changes"]
    assert after["harness_status"] == "completed" and after["case_tool_calls"] > 0
    assert "exactly four digits" not in after["assistant_message"]


@pytest.mark.parametrize("failure_stage", [IdentityExtraction, TurnUnderstanding])
def test_either_extraction_failure_stops_without_changing_identity(monkeypatch, failure_stage):
    def structured(schema, *args):
        if schema is failure_stage:
            raise llm.LLMUnavailable("LLM unavailable")
        return IdentityExtraction()
    monkeypatch.setattr(llm, "structured_call", structured)
    with pytest.raises(llm.LLMUnavailable):
        llm.extract_turn_with_llm(FOLLOWUP)

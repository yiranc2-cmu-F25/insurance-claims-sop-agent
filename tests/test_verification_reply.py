"""VERIFY_ID wording comes from the model; facts, options and the gate come from code."""
from unittest.mock import Mock
from uuid import uuid4

import pytest

from claim_agent.llm import client as llm
from claim_agent.llm.schemas import TurnExtraction, VerificationReply
from claim_agent.services import verification_reply
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


def run(monkeypatch, text, **fields):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", **fields))
    return graph.invoke({"user_message": text}, config={"configurable": {"thread_id": uuid4().hex}})


def test_composer_receives_labels_and_counts_but_never_identity_values(monkeypatch):
    calls = []
    def compose(facts):
        calls.append(facts)
        return VerificationReply(reply=(
            "I hear you, and I'm sorry this feels like a runaround. Claim details are protected, so I have to "
            "confirm it's you first: I have your name and need two more of your date of birth, phone number, "
            "email address or ID last four digits. I've noted your question about the denial and will go straight to it."
        ))
    monkeypatch.setattr(llm, "compose_verification_reply", compose)
    state = run(monkeypatch, "I already told you I'm Margaret Chen. This is ridiculous. Why was my claim denied?",
                name="Margaret Chen", intent="denial_question", emotion="angry", needs_human_support=True)
    facts = calls[-1]
    assert "Margaret" not in repr(facts) and "[identity redacted]" in facts["caller_message"]
    assert facts["received_fields"] == ["full name"] and facts["remaining_fields"] == 2
    assert facts["emotion"] == "angry" and facts["offer_human"] is False
    assert facts["remembered_request"] == "Understand the denial reason"
    assert facts["accepted_fields"] == ["date of birth", "phone number", "email address",
                                        "last four digits of the ID on your policy (SSN or national ID)"]
    assert state["assistant_message"].startswith("I hear you") and state["phase"] == "VERIFY_ID"
    assert not state.get("verified_party_id") and state["requested_intent"] == "denial_question"
    assert state.get("handoff_status", "none") == "none" and state["case_tool_calls"] == 0


@pytest.mark.parametrize("bad", [
    "You're verified now, thanks.",
    "Your claim CL-2048 was denied for missing documents.",
    "Please send your full SSN 123456789 so I can check.",
    "Thanks for calling, goodbye.",
    "",
    "x" * 901,
])
def test_off_script_wording_falls_back_to_the_fixed_template(monkeypatch, bad):
    monkeypatch.setattr(llm, "compose_verification_reply", lambda facts: Mock(reply=bad))
    state = run(monkeypatch, "My name is Margaret Chen.", name="Margaret Chen")
    assert "2 more identity details" in state["assistant_message"]
    assert "date of birth" in state["assistant_message"]


def test_model_failure_keeps_the_template_and_does_not_mark_the_turn_unavailable(monkeypatch):
    monkeypatch.setattr(llm, "compose_verification_reply", Mock(side_effect=llm.LLMUnavailable()))
    state = run(monkeypatch, "My name is Margaret Chen.", name="Margaret Chen")
    assert "2 more identity details" in state["assistant_message"]
    assert state["llm_available"] is True and state["llm_error"] is False


def test_escalation_and_delegate_context_reach_the_composer(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "compose_verification_reply", lambda facts: calls.append(facts) or Mock(reply=""))
    config = {"configurable": {"thread_id": uuid4().hex}}
    for _ in range(3):
        monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
            scope="in_scope", emotion="frustrated", caller_role="delegate", name="David Chen"))
        state = graph.invoke({"user_message": "Just help me with my mother's claim."}, config=config)
    assert calls[-1]["offer_human"] is True and calls[-1]["caller_role"] == "delegate"
    assert calls[-1]["delegate_note"] and calls[-1]["consecutive_distressed_turns"] == 3
    assert state["handoff_reason"] == "emotional_support"
    assert "policy number" in state["assistant_message"]  # template fallback keeps the delegate note


def test_acceptable_requires_an_offered_field_and_no_protected_data():
    assert verification_reply.acceptable("Could you share your date of birth or phone number?", ["dob", "phone"])
    assert not verification_reply.acceptable("Could you share your date of birth?", ["phone", "email"])
    assert verification_reply.acceptable("The details did not match our records; please double-check them.", [])
    assert not verification_reply.acceptable("Your policy POL-9921 is on file; share your phone.", ["phone"])
    assert not verification_reply.acceptable("Email me at a@b.com with your phone.", ["phone"])


def test_template_names_the_id_type_neutrally_after_a_mismatch(monkeypatch):
    monkeypatch.setattr(llm, "compose_verification_reply", Mock(side_effect=llm.LLMUnavailable()))
    state = run(monkeypatch, "I'm Ma Tian, DOB 1964-09-10, SSN last four 6688.",
                name="Ma Tian", dob="1964-09-10", id_last4="6688", id_type="ssn_last4")
    assert not state.get("verified_party_id")
    assert "SSN or national ID" in state["assistant_message"] and "national_id" not in state["assistant_message"]


def test_stock_opener_is_stripped_from_model_wording(monkeypatch):
    monkeypatch.setattr(llm, "compose_verification_reply", lambda facts: Mock(
        reply="I understand that this situation is frustrating for you. I still need your date of birth or phone number."))
    state = run(monkeypatch, "This is ridiculous.", name="Margaret Chen", emotion="frustrated")
    assert state["assistant_message"].startswith("I still need your date of birth")

"""Fixture relationships through the real graph, with explicit model responses."""
from copy import deepcopy
from datetime import date
from uuid import uuid4
from unittest.mock import Mock

import pytest

from claim_agent.guardrails.policy import ACTION_TOOLS
from claim_agent.llm import client as llm
from claim_agent.llm.schemas import TurnExtraction, CasePlan, GroundedAnswer
from claim_agent.services.case_harness import run_case
from claim_agent.services.email_followup import choose_email
from claim_agent.tools import get_claim_field_definitions, get_claim_for_action, get_document_guidance_for_claim
from claim_agent.tools import identity
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph


OWNER = dict(name="Margaret Chen", dob="1985-03-15", id_last4="4472")
DELEGATE = dict(caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
                represented_policy_number="POL-9921")


@pytest.fixture
def conversation(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    def turn(text="Insurance question", **extraction):
        monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
            scope="in_scope", **extraction,
        ))
        return graph.invoke({"user_message": text}, config=config)
    return turn


def test_delegate_partial_identity_retains_early_case_hint(conversation):
    first = conversation(caller_role="delegate", name="David Chen", represented_name="Margaret Chen",
                         intent="denial_question", case_type="healthcare", status="denied", month="January")
    assert first["phase"] == "VERIFY_ID" and not first.get("verified_party_id")
    assert "pathology" not in first["assistant_message"]
    state = conversation(dob="2004-06-20", id_last4="6028")
    assert state["verified_caller_id"] == "REP-1"
    assert state["verified_party_id"] == "P9"
    assert state["selected_claim_id"] == "CL-2048" and state["phase"] == "POST_PROCESS"
    assert "pathology" in state["assistant_message"]


def test_customer_pii_cannot_authenticate_delegate(conversation):
    state = conversation(**OWNER, caller_role="delegate", represented_policy_number="POL-9921",
                         intent="status_inquiry", case_id="CL-2048")
    assert state["phase"] == "VERIFY_ID" and not state.get("verified_party_id")
    assert not state.get("selected_claim_id")


@pytest.mark.parametrize("grant_update", [
    {"status": "revoked"}, {"expires_at": "2000-01-01"}, {"valid_from": "2099-01-01"},
    {"expires_at": None},
])
def test_invalid_delegate_grant_blocks_before_claim_disclosure(monkeypatch, conversation, grant_update):
    rows = deepcopy(identity.load_representatives())
    rows[0]["authorization"].update(grant_update)
    monkeypatch.setattr(identity, "load_representatives", lambda: rows)
    state = conversation(**DELEGATE, intent="denial_question", case_id="CL-2048")
    assert state["phase"] == "VERIFY_ID"
    assert not state.get("verified_party_id") and not state.get("selected_claim_id")
    assert state["handoff_reason"] == "delegate_authorization"
    assert "pathology" not in state["assistant_message"]


def test_relationship_only_grants_nothing(monkeypatch, conversation):
    rows = deepcopy(identity.load_representatives())
    rows[0].pop("authorization")
    monkeypatch.setattr(identity, "load_representatives", lambda: rows)
    state = conversation(**DELEGATE, intent="status_inquiry", case_id="CL-2048")
    assert not state.get("verified_party_id")


def test_delegate_needs_customer_reference_then_can_continue(conversation):
    state = conversation(caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
                         intent="status_inquiry", case_id="CL-2048")
    assert state["phase"] == "VERIFY_ID"
    assert "policyholder's full name or policy number" in state["assistant_message"]
    state = conversation(represented_policy_number="POL-9921")
    assert state["phase"] == "POST_PROCESS"
    assert state["selected_claim_id"] == "CL-2048"


def test_delegate_cannot_switch_to_unrelated_customer(conversation):
    state = conversation(**DELEGATE, intent="status_inquiry", case_id="CL-2048")
    assert state["verified_party_id"] == "P9"
    state = conversation(represented_policy_number="POL-8836", intent="status_inquiry", case_id="CL-3001")
    assert state["phase"] == "VERIFY_ID" and not state.get("verified_party_id")
    assert state["discussed_answers"] == [] and state["email_offer"] is None
    assert not state["selected_claim_id"]


def test_role_switch_cannot_reuse_customer_identity(conversation):
    conversation(**OWNER, intent="status_inquiry", case_id="CL-2048")
    state = conversation(caller_role="delegate", represented_name="Margaret Chen")
    assert state["phase"] == "VERIFY_ID" and state["collected_pii"] == {}
    assert state["email_offer"] is None


def test_delegate_action_and_claim_scopes(monkeypatch, conversation):
    rows = deepcopy(identity.load_representatives())
    rows[0]["authorization"]["actions"] = ["read_claim_status"]
    rows[0]["authorization"]["claim_ids"] = ["CL-2048"]
    monkeypatch.setattr(identity, "load_representatives", lambda: rows)
    state = conversation(**DELEGATE, intent="status_inquiry", case_id="CL-2048")
    assert state["phase"] == "POST_PROCESS"
    # Summary follow-up items respect the grant: no deadline/document reads without permission.
    assert "not available under the current authorization" in state["email_offer"]["summary"]
    assert "2026-03-18" not in state["email_offer"]["summary"]
    with pytest.raises(ValueError):
        choose_email(state, state["email_offer"]["id"], "send")
    state = conversation(intent="payment_question")
    assert state["authorization_denied"] and not state["selected_claim_id"]
    state = conversation(intent="status_inquiry", case_id="CL-2011", request_mode="replace")
    assert not state["selected_claim_id"] and "accessible claim" in state["assistant_message"]


def test_revocation_is_rechecked_at_tool_and_email_boundaries(monkeypatch, conversation):
    state = conversation(**DELEGATE, intent="status_inquiry", case_id="CL-2048")
    rows = deepcopy(identity.load_representatives())
    rows[0]["authorization"]["status"] = "revoked"
    monkeypatch.setattr(identity, "load_representatives", lambda: rows)
    planner = Mock()
    monkeypatch.setattr(llm, "plan_case_with_llm", planner)
    result = run_case({**state, "phase": "PROCESS_CASE"})
    assert result["harness_status"] == "authorization_denied"
    planner.assert_not_called()
    with pytest.raises(ValueError):
        choose_email(state, state["email_offer"]["id"], "send")


def test_explicit_claim_selection_and_case_change_drop_stale_hints(conversation):
    state = conversation(**OWNER, intent="status_inquiry", case_type="healthcare", month="January")
    assert not state.get("selected_claim_id") and "multiple claims" in state["assistant_message"]
    state = conversation(case_id="cl-2011")
    assert state["selected_claim_id"] == "CL-2011"
    state = conversation(intent="status_inquiry", case_type="auto", new_case=True)
    assert state["intent_hint"] == {"case_type": "auto"}
    assert state["selected_claim_id"] == "CL-2102"
    assert "CL-2011" not in state["email_offer"]["summary"]


def test_claim_id_cannot_access_other_customer(conversation):
    state = conversation(**OWNER, intent="status_inquiry", case_id="CL-3001")
    assert not state.get("selected_claim_id")
    assert "accessible claim matching" in state["assistant_message"]
    assert "diagnosis report" not in state["assistant_message"]


def test_customer_with_no_claims_gets_clear_response(conversation):
    state = conversation(name="Ava Lopez", dob="1990-08-21", id_last4="9180", intent="status_inquiry")
    assert state["verified_party_id"] == "P7" and not state.get("selected_claim_id")
    assert "couldn't find any claims" in state["assistant_message"]


def test_amount_values_and_meanings_reach_model_without_examples(monkeypatch, conversation):
    def compose(question, intent, evidence, *args):
        assert intent == "payment_question"
        claim = evidence["get_claim_for_action"]
        definitions = evidence["get_claim_field_definitions"]
        assert claim["net_pay"] == "780.00"
        assert definitions["currency"] == "USD"
        assert "net_fee" in definitions["fields"]
        assert all("example" not in item for item in definitions["fields"].values())
        return GroundedAnswer(answer="The finalized insurer payment is 780.00 USD.", sources=list(evidence))
    monkeypatch.setattr(llm, "compose_case_answer", compose)
    state = conversation(**OWNER, intent="payment_question", case_id="CL-2011")
    assert state["harness_status"] == "completed" and state["case_tool_calls"] == 2
    assert state["authorized_action"] == "read_claim_amounts"
    assert get_claim_field_definitions("P9", "CL-3001") is None
    assert "net_pay" not in get_claim_for_action("P9", "CL-2011", "read_claim_status")


def test_harness_inserts_amount_field_meanings_when_model_omits_them(monkeypatch, conversation):
    monkeypatch.setattr(llm, "plan_case_with_llm", lambda *a: CasePlan(
        decision="execute", tools=["get_claim_for_action"], followup_topic=None))
    state = conversation(**OWNER, intent="payment_question", case_id="CL-2011")
    assert state["harness_status"] == "completed" and state["case_tool_calls"] == 2
    assert [event["tool"] for event in state["audit_events"] if event["event"] == "tool"] == [
        "get_claim_for_action", "get_claim_field_definitions",
    ]


def test_document_alternatives_and_exhaustion_offer_human(monkeypatch, conversation):
    def compose(question, intent, evidence, *args):
        docs = evidence["get_document_guidance_for_claim"]
        assert "replacement copy" in docs["alternatives"]["pathology report"]
        assert "visit summary" in docs["alternatives"]["office note"]
        assert "manual options" in docs["human_review"]
        return GroundedAnswer(answer=docs["human_review"], sources=["get_document_guidance_for_claim"])
    monkeypatch.setattr(llm, "compose_case_answer", compose)
    state = conversation(**OWNER, intent="document_submission", case_id="CL-2048", document_alternatives_exhausted=True)
    assert state["harness_status"] == "completed"
    assert state["handoff_status"] == "offered" and state["handoff_reason"] == "documents_exhausted"


def test_missing_specific_guidance_does_not_invent_documents():
    missing = get_document_guidance_for_claim("P12", "CL-3001")
    assert missing["documents"] == {"diagnosis report": ""}
    assert missing["alternatives"] == {"diagnosis report": ""}
    assert missing["default_alternative"]
    auto = get_document_guidance_for_claim("P9", "CL-2102")
    assert auto["documents"] == {} and auto["alternatives"] == {}


def test_exhausted_document_memory_survives_identity_answers(conversation):
    state = conversation(intent="document_submission", case_id="CL-2048", document_alternatives_exhausted=True)
    assert state["phase"] == "VERIFY_ID"
    state = conversation(**OWNER)
    assert state["handoff_reason"] == "documents_exhausted"
    state = conversation(intent="payment_question")
    assert state["harness_status"] == "completed"
    state = conversation(intent="status_inquiry")
    assert state["harness_status"] == "completed"
    state = conversation(intent="status_inquiry", case_id="CL-2011", new_case=True)
    assert not state["document_alternatives_exhausted"]


def test_partial_delegate_target_preserves_early_claim_hint(conversation):
    conversation(intent="denial_question", case_type="healthcare", status="denied", month="January")
    state = conversation(**DELEGATE)
    assert state["selected_claim_id"] == "CL-2048" and state["phase"] == "POST_PROCESS"


@pytest.mark.parametrize("action", ["read_claim_status", "read_denial_reason", "read_claim_summary", "read_next_steps", "read_document_guidance"])
def test_guideline_tools_are_available_for_fixture_intents(action):
    assert {"get_document_guidance_for_claim", "get_claim_followup_guidance"} <= ACTION_TOOLS[action]


def test_identity_email_punctuation_and_id_type_are_not_discarded():
    assert identity.verify_identity(dict(name="Margaret Chen", dob="1985-03-15", email="margaret@em.ail.com"))[0] is None
    assert identity.verify_identity({**OWNER, "id_type": "national_id_last4"})[0] is None
    assert identity.get_delegate_authorization("REP-1", "P9", today=date(2031, 1, 1)) is None


def test_expired_deadline_flag_reaches_the_answer_model(monkeypatch, conversation):
    from datetime import date
    from claim_agent.tools import claims
    monkeypatch.setattr(claims, "current_date", lambda: date(2026, 9, 30))
    seen = {}
    def compose(question, intent, evidence, *args):
        seen.update(evidence["get_claim_for_action"])
        return GroundedAnswer(answer="The recorded appeal deadline of 2026-03-18 has already passed as of 2026-09-30.",
                              sources=["get_claim_for_action"])
    monkeypatch.setattr(llm, "compose_case_answer", compose)
    state = conversation(**OWNER, intent="appeal_deadline", case_id="CL-2048")
    assert state["harness_status"] == "completed" and seen["appeal_deadline_passed"] is True
    assert "already passed" in state["email_offer"]["summary"]


def test_registered_aliases_verify_and_resolve_the_same_customer(conversation):
    assert identity.verify_identity({"name": "Yaven Li", "dob": "1989-12-03", "email": "yawen.li@example.com"}) == ("P13", ["name", "dob", "email"])
    assert identity.verify_identity({"name": "Ya Wen Li", "dob": "1989-12-03", "id_last4": "5317"})[0] == "P13"
    assert identity.verify_identity({"name": "Yawen Lee", "dob": "1989-12-03", "id_last4": "5317"})[0] is None
    assert identity.resolve_represented_customer({"name": "Yaven Li"}) == "P13"
    # Through the workflow: the alias verifies, and the fixture customer simply has no claims yet.
    state = conversation(name="Yaven Li", dob="1989-12-03", email="yawen.li@example.com", intent="status_inquiry")
    assert state["verified_party_id"] == "P13" and not state.get("selected_claim_id")
    assert "couldn't find any claims" in state["assistant_message"] and state["case_tool_calls"] == 0

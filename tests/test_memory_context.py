from copy import deepcopy
from time import time
from unittest.mock import Mock
from uuid import uuid4

from claim_agent.llm.schemas import TurnExtraction
from claim_agent.llm import client as llm
from claim_agent.services import memory_policy as memory
from claim_agent.services.case_memory import accessible_case_notes, prune_cases
from claim_agent.services.verification_session import expire_identity
from claim_agent.tools import identity
from claim_agent.workflow import intake
from claim_agent.workflow.graph import graph

OWNER = dict(name="Margaret Chen", dob="1985-03-15", id_last4="4472")


def test_messages_are_bounded_and_old_ones_expire(monkeypatch):
    monkeypatch.setattr(memory, "time", lambda: 100000.0)
    messages = [{"role": "user", "content": str(i), "at": 99999.0} for i in range(30)]
    result = memory.bounded_messages([], messages)
    assert len(result) == memory.MAX_MESSAGES and result[0]["content"] == "10"
    assert not memory.bounded_messages([], [{"role": "user", "content": "old", "at": 1.0}])


def test_recent_context_is_redacted_bounded_and_untrusted(monkeypatch):
    session = {"configurable": {"thread_id": uuid4().hex}}
    original = intake.extract_turn_with_llm
    calls = []
    def inspect(text, context):
        calls.append(context)
        return original(text, context=context)
    monkeypatch.setattr(intake, "extract_turn_with_llm", inspect)
    graph.invoke({"user_message": "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472"}, config=session)
    graph.invoke({"user_message": "What is the status of my denied healthcare claim from January?"}, config=session)
    context = calls[-1]["recent_messages"]
    assert context and len(context) <= memory.CONTEXT_MESSAGES
    assert all(value not in repr(context) for value in OWNER.values())
    assert "identity redacted" in repr(context)
    state = graph.get_state(session).values
    for i in range(15):
        state = graph.invoke({"user_message": "Thanks"}, config=session)
    assert len(state["messages"]) <= memory.MAX_MESSAGES


def test_case_switch_keeps_separate_notes_and_fresh_tools_are_required(monkeypatch):
    session = {"configurable": {"thread_id": uuid4().hex}}
    contexts = []
    def turn(**fields):
        def extract(*a, context):
            contexts.append(context)
            return TurnExtraction(scope="in_scope", **fields)
        monkeypatch.setattr(intake, "extract_turn_with_llm", extract)
        return graph.invoke({"user_message": "My claim question"}, config=session)
    first = turn(**OWNER, intent="denial_question", case_id="CL-2048")
    second = turn(intent="status_inquiry", case_id="CL-2011", new_case=True)
    assert set(second["case_memories"]) == {"CL-2048", "CL-2011"}
    assert "CL-2048" not in second["email_offer"]["summary"]
    query = Mock(side_effect=TimeoutError())
    monkeypatch.setattr("claim_agent.services.case_harness.tools.get_claim_for_action", query)
    failed = turn(intent="denial_question", case_id="CL-2048", new_case=True)
    assert {n["claim_id"] for n in contexts[-1]["case_notes"]} == {"CL-2048", "CL-2011"}
    assert failed["harness_status"] == "tool_failed" and not failed["email_offer_pending"]
    assert "pathology report" not in failed["assistant_message"]
    query.assert_called_once()


def test_note_access_rechecked_after_delegate_grant_revocation(monkeypatch):
    session = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", caller_role="delegate", name="David Chen", dob="2004-06-20", id_last4="6028",
        represented_policy_number="POL-9921", intent="status_inquiry", case_id="CL-2048"))
    state = graph.invoke({"user_message": "Please check her claim"}, config=session)
    assert accessible_case_notes(state)
    rows = deepcopy(identity.load_representatives())
    rows[0]["authorization"]["status"] = "revoked"
    monkeypatch.setattr(identity, "load_representatives", lambda: rows)
    assert not accessible_case_notes(state)
    assert all("CL-2048" not in m["content"] for m in memory.recent_context(state) if m["role"] == "assistant")


def test_notes_cannot_cross_identity_and_expiry_clears_model_context():
    now = time()
    state = {"verified_party_id": "P9", "verified_caller_id": "P9", "verified_at": now,
             "verified_last_active_at": now, "verification_matches": ["name", "dob", "id_last4"],
             "messages": [{"role": "assistant", "content": "Claim CL-2048 is denied"}],
             "case_memories": {"CL-2048": {"updated_at": now, "identity": ["P12", "P12", "policyholder"], "entries": []}}}
    assert not accessible_case_notes(state)
    state["verified_last_active_at"] = now - 1000
    expired = expire_identity(state)
    assert expired["case_memories"] == {} and expired["messages"] == []


def test_case_notes_have_a_size_and_age_bound():
    now = time()
    entries = {str(i): {"updated_at": now - i} for i in range(10)}
    assert len(prune_cases(entries)) == memory.MAX_CASES
    assert not prune_cases({"old": {"updated_at": 1}})

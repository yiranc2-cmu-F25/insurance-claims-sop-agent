import json
from time import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import SESSION_COOKIE, app
from claim_agent.services import email_followup
from claim_agent.llm import client as llm
from claim_agent.workflow import intake, resolve_intent, verify_id
from claim_agent.workflow.graph import graph
from claim_agent.services.handoff import build_handoff_summary, offer_handoff, request_handoff
from claim_agent.llm.schemas import TurnExtraction


VERIFIED_MESSAGE = (
    "My name is Margaret Chen, DOB is 1985-03-15, SSN last four is 4472. "
    "What is the status of my denied healthcare claim from January?"
)


@pytest.mark.parametrize("phase", ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"])
def test_explicit_human_request_stops_before_business_tools(monkeypatch, phase):
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.update_state(config, {"phase": phase}, as_node="pause_gate")
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        intent="representative_request", scope="in_scope",
    ))
    verify = Mock()
    query = Mock()
    email = Mock()
    monkeypatch.setattr(verify_id, "verify_identity", verify)
    monkeypatch.setattr(resolve_intent, "select_claim", query)
    monkeypatch.setattr(email_followup, "send_email_summary", email)
    state = graph.invoke({"user_message": "I need a person to help."}, config=config)
    assert state["phase"] == phase
    assert state["handoff_status"] == "offered"
    assert state["handoff_reason"] == "customer_request"
    verify.assert_not_called()
    query.assert_not_called()
    email.assert_not_called()


def test_single_distressed_turn_is_answered_by_the_workflow_not_a_transfer(monkeypatch):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", emotion="angry", needs_human_support=True, intent="denial_question", name="Margaret Chen",
    ))
    state = graph.invoke({"user_message": "I already told you who I am. This is ridiculous. Why was it denied?"},
                         config={"configurable": {"thread_id": uuid4().hex}})
    assert state.get("handoff_status", "none") == "none"
    assert state["phase"] == "VERIFY_ID" and "identity" in state["assistant_message"]
    assert "denied" not in state["assistant_message"].lower()
    assert state["distress_turns"] == 1 and state["requested_intent"] == "denial_question"


def test_sustained_distress_offers_human_while_verification_continues(monkeypatch):
    def turn(config, emotion, needs_help):
        monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
            scope="in_scope", emotion=emotion, needs_human_support=needs_help,
        ))
        return graph.invoke({"user_message": "This is difficult for me."}, config=config)
    flagged = {"configurable": {"thread_id": uuid4().hex}}
    assert turn(flagged, "frustrated", True).get("handoff_status", "none") == "none"
    second = turn(flagged, "angry", True)
    assert second["handoff_status"] == "offered" and second["handoff_reason"] == "emotional_support"
    assert second["phase"] == "VERIFY_ID" and "identity" in second["assistant_message"]
    unflagged = {"configurable": {"thread_id": uuid4().hex}}
    for _ in range(2):
        assert turn(unflagged, "anxious", False).get("handoff_status", "none") == "none"
    assert turn(unflagged, "anxious", False)["handoff_reason"] == "emotional_support"
    assert turn(unflagged, "neutral", False)["distress_turns"] == 0


def test_verification_progress_clears_a_soft_transfer_offer(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="in_scope", emotion="angry"))
    for _ in range(3):
        state = graph.invoke({"user_message": "Still upset."}, config=config)
    assert state["handoff_reason"] == "emotional_support"
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", name="Margaret Chen", dob="1985-03-15", id_last4="4472", intent="status_inquiry", case_id="CL-2048",
    ))
    state = graph.invoke({"user_message": "Fine, here are my details."}, config=config)
    assert state["verified_party_id"] == "P9" and state.get("handoff_status", "none") == "none"


def test_refusal_and_repeated_out_of_scope_offer_human(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", emotion="refusing",
    ))
    for _ in range(2):
        state = graph.invoke({"user_message": "I'd rather not provide that."}, config=config)
    assert state["handoff_reason"] == "verification_refused"
    config = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(scope="out_of_scope"))
    for _ in range(2):
        state = graph.invoke({"user_message": "Explain unrelated science."}, config=config)
    assert state["handoff_status"] == "offered" and state["handoff_reason"] == "out_of_scope"


def test_unsupported_request_can_offer_before_verification(monkeypatch):
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        intent="claim_update", scope="in_scope",
    ))
    state = graph.invoke({"user_message": "Modify the claim for me."},
                         config={"configurable": {"thread_id": uuid4().hex}})
    assert state["handoff_reason"] == "unsupported_request"
    assert state["phase"] == "VERIFY_ID" and not state.get("verified_party_id")


def test_tool_failure_offers_human(monkeypatch):
    monkeypatch.setattr("claim_agent.services.case_harness.tools.get_claim_for_action", Mock(side_effect=TimeoutError()))
    state = graph.invoke({"user_message": VERIFIED_MESSAGE},
                         config={"configurable": {"thread_id": uuid4().hex}})
    assert state["handoff_status"] == "offered"
    assert state["handoff_reason"] == "tool_failure"


def test_unverified_summary_excludes_raw_identity_and_claim_data():
    state = {
        **offer_handoff("customer_request"), "phase": "VERIFY_ID",
        "collected_pii": {"name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
        "requested_question": "Secret medical details and policy POL-9921",
        "messages": [{"role": "user", "content": "private@email.com"}],
        "discussed_answers": ["CL-2048 was denied; private medical information"],
        "grounded_claim": {"case_id": "CL-2048", "status": "denied"},
    }
    result = request_handoff(state)
    summary = result["handoff_summary"]
    assert summary["verification"] == "required" and summary["discussed_items"] == []
    text = json.dumps(summary)
    for forbidden in ("Margaret", "1985-03-15", "4472", "POL-9921", "private", "CL-2048"):
        assert forbidden not in text


def test_verified_summary_only_uses_validated_answers_and_redacts_identity():
    now = time()
    summary = build_handoff_summary({
        "verified_at": now, "verified_last_active_at": now,
        **offer_handoff("tool_failure"),
        "verified_party_id": "P9", "verification_matches": ["name", "dob", "id_last4"],
        "collected_pii": {"name": "Margaret Chen", "id_last4": "4472"},
        "requested_intent": "status_inquiry",
        "requested_question": "Unvalidated user accusation",
        "discussed_answers": ["Claim CL-2048 is denied. Margaret Chen; 4472; test@example.com"],
    })
    assert summary["verification"] == "verified"
    assert "CL-2048" in summary["discussed_items"][0]
    assert "Unvalidated" not in json.dumps(summary)
    assert all(s not in json.dumps(summary) for s in ("Margaret", "4472", "test@example.com"))


def test_api_handoff_lifecycle_idempotency_pause_and_resume(monkeypatch):
    with TestClient(app) as client:
        assert client.post("/api/handoff").status_code == 409
        before = client.post("/api/chat", json={"message": "Please talk to a human representative."}).json()
        assert before["handoff"]["status"] == "offered" and not before["verified"]
        create = client.post("/api/handoff").json()
        request_id = create["handoff"]["request_id"]
        assert request_id.startswith("DEMO-") and create["handoff"]["simulated"] is True
        assert create["phase"] == "VERIFY_ID"
        assert client.post("/api/handoff").json()["handoff"]["request_id"] == request_id
        assert client.get("/api/conversation").json()["handoff"]["status"] == "requested"
        extractor = Mock(side_effect=AssertionError("Must not invoke the model while paused"))
        original = intake.extract_turn_with_llm
        monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
        paused = client.post("/api/chat", json={"message": VERIFIED_MESSAGE})
        assert paused.status_code == 409
        extractor.assert_not_called()
        config = {"configurable": {"thread_id": client.cookies[SESSION_COOKIE]}}
        assert VERIFIED_MESSAGE not in json.dumps(graph.get_state(config).values)
        resumed = client.post("/api/handoff/resume").json()
        assert resumed["handoff"]["status"] == "none" and resumed["phase"] == "VERIFY_ID"
        assert not resumed["verified"]
        monkeypatch.setattr(intake, "extract_turn_with_llm", original)
        after = client.post("/api/chat", json={"message": VERIFIED_MESSAGE}).json()
        assert after["verified"] and after["phase"] == "POST_PROCESS"


def test_direct_graph_call_also_respects_pause(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.update_state(config, {"phase": "VERIFY_ID", "handoff_status": "requested"}, as_node="pause_gate")
    extractor = Mock()
    monkeypatch.setattr(intake, "extract_turn_with_llm", extractor)
    state = graph.invoke({"user_message": VERIFIED_MESSAGE}, config=config)
    assert state["handoff_status"] == "requested" and state["phase"] == "VERIFY_ID"
    assert not state.get("verified_party_id")
    extractor.assert_not_called()


def test_return_to_bot_preserves_verified_claim_and_email_choice():
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": VERIFIED_MESSAGE}).json()
        client.post("/api/chat", json={"message": "I want a human representative."})
        request = client.post("/api/handoff").json()
        assert request["handoff"]["summary"]["verification"] == "verified"
        resumed = client.post("/api/handoff/resume").json()
        assert resumed["verified"] is True and resumed["claim_id"] == first["claim_id"]
        assert resumed["phase"] == "POST_PROCESS"
        assert "email summary" in resumed["reply"]
        followup = client.post("/api/chat", json={"message": "What is the appeal deadline?"}).json()
        assert followup["authorized_action"] == "read_appeal_deadline"


def test_sessions_do_not_share_handoff_requests():
    with TestClient(app) as one, TestClient(app) as two:
        one.post("/api/chat", json={"message": "Talk to a human representative."})
        first = one.post("/api/handoff").json()
        other = two.get("/api/conversation").json()
        assert other["handoff"]["status"] == "none"
        assert other["handoff"]["request_id"] is None
        assert two.post("/api/handoff").status_code == 409
        two.post("/api/handoff/resume")
        assert one.get("/api/conversation").json()["handoff"]["request_id"] == first["handoff"]["request_id"]


def test_model_unavailable_still_allows_click_to_handoff(monkeypatch):
    monkeypatch.setattr(intake, "extract_turn_with_llm", Mock(side_effect=llm.LLMUnavailable()))
    with TestClient(app) as client:
        result = client.post("/api/chat", json={"message": "hello"})
        assert result.status_code == 503
        assert result.json()["handoff"]["reason"] == "service_unavailable"
        created = client.post("/api/handoff")
        assert created.status_code == 200 and created.json()["handoff"]["status"] == "requested"


def test_concurrent_clicks_create_one_request():
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": "Talk to a human representative."})
        cookie = client.cookies[SESSION_COOKIE]
    def click(_):
        with TestClient(app) as tab:
            tab.cookies.set(SESSION_COOKIE, cookie)
            response = tab.post("/api/handoff")
            assert response.status_code == 200
            return response.json()["handoff"]["request_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(click, range(4)))
    assert len(set(ids)) == 1


def test_later_answer_clears_a_stale_review_offer(monkeypatch):
    config = {"configurable": {"thread_id": uuid4().hex}}
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", name="Margaret Chen", dob="1985-03-15", id_last4="4472", intent="claim_update"))
    state = graph.invoke({"user_message": "Change my address on the claim."}, config=config)
    assert state["handoff_reason"] == "unsupported_request"
    monkeypatch.setattr(intake, "extract_turn_with_llm", lambda *a, **kw: TurnExtraction(
        scope="in_scope", intent="appeal_deadline", case_id="CL-2048"))
    state = graph.invoke({"user_message": "What is the appeal deadline for CL-2048?"}, config=config)
    assert state["harness_status"] == "completed" and state.get("handoff_status", "none") == "none"

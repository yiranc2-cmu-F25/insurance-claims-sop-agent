from claim_agent.workflow.graph import graph


def _verified_message(request: str) -> str:
    return (
        "My name is Margaret Chen, DOB is 1985-03-15, "
        "SSN last four is 4472, policy POL-9921. " + request
    )


def test_document_request_uses_document_action():
    state = graph.invoke(
        {"user_message": _verified_message("What documents do I need for my denied healthcare claim from January?")},
        config={"configurable": {"thread_id": "test-document-intent"}},
    )
    assert state["authorized_action"] == "read_document_guidance"
    assert "requested documents" in state["assistant_message"]


def test_appeal_deadline_request_uses_deadline_action():
    state = graph.invoke(
        {"user_message": _verified_message("What is the appeal deadline for my denied healthcare claim from January?")},
        config={"configurable": {"thread_id": "test-deadline-intent"}},
    )
    assert state["authorized_action"] == "read_appeal_deadline"
    assert "2026-03-18" in state["assistant_message"]


def test_claim_update_is_not_a_self_service_permission():
    state = graph.invoke(
        {"user_message": _verified_message("Please update my claim.")},
        config={"configurable": {"thread_id": "test-update-intent"}},
    )
    assert state["authorization_denied"] is True
    assert "human claims representative" in state["assistant_message"]

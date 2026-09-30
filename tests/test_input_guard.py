from claim_agent.workflow.graph import graph


def test_full_ssn_is_blocked_before_pii_processing():
    config = {"configurable": {"thread_id": "test-full-ssn"}}
    state = graph.invoke(
        {"user_message": "My full SSN is 123-45-6789."},
        config=config,
    )
    assert state["input_block_reason"] == "full_ssn"
    assert "only provide the last four" in state["assistant_message"].lower()


def test_conflicting_identity_values_are_stopped():
    config = {"configurable": {"thread_id": "test-conflict"}}
    graph.invoke(
        {"user_message": "My name is Margaret Chen and my DOB is 1985-03-15."},
        config=config,
    )
    state = graph.invoke(
        {"user_message": "Actually my DOB is 1986-03-15."},
        config=config,
    )
    assert state["phase"] == "VERIFY_ID"
    assert "conflicting values" in state["assistant_message"]

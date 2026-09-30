from claim_agent.workflow.graph import graph


def test_unavailable_llm_does_not_guess_intent_or_query_claim(monkeypatch):
    monkeypatch.setattr("claim_agent.workflow.intake.extract_turn_with_llm", lambda text, **kwargs: None)
    state = graph.invoke(
        {
            "user_message": (
                "My name is Margaret Chen, DOB is 1985-03-15, "
                "SSN last four is 4472. What is the status of my denied healthcare claim from January?"
            )
        },
        config={"configurable": {"thread_id": "test-llm-required"}},
    )

    assert state.get("selected_claim_id") is None
    assert state.get("authorized_action") is None
    assert "language model is unavailable" in state["assistant_message"].lower()

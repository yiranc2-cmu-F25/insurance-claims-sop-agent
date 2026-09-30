from claim_agent.workflow.graph import graph


def test_sample_case_reaches_post_process():
    config = {"configurable": {"thread_id": "test-sample"}}
    state = graph.invoke(
        {
            "user_message": (
                "I am the policyholder. My name is Margaret Chen, policy POL-9921. "
                "I am calling about my denied healthcare claim from January. "
                "DOB is 1985-03-15, SSN last four is 4472."
            )
        },
        config=config,
    )
    assert state["verified_party_id"] == "P9"
    assert state["selected_claim_id"] == "CL-2048"
    assert state["phase"] == "POST_PROCESS"
    assert "pathology" in state["assistant_message"]


def test_user_can_ask_a_second_claim_question_after_processing():
    config = {"configurable": {"thread_id": "test-follow-up-question"}}
    graph.invoke(
        {
            "user_message": (
                "My name is Margaret Chen, DOB is 1985-03-15, "
                "SSN last four is 4472. What is the denial reason for my denied healthcare claim from January?"
            )
        },
        config=config,
    )

    state = graph.invoke(
        {"user_message": "What is the appeal deadline?"},
        config=config,
    )

    assert state["phase"] == "POST_PROCESS"
    assert state["authorized_action"] == "read_appeal_deadline"
    assert "2026-03-18" in state["assistant_message"]


def test_email_chat_rejection_does_not_confirm_button_choice():
    config = {"configurable": {"thread_id": "test-email-rejection"}}
    graph.invoke(
        {
            "user_message": (
                "My name is Margaret Chen, DOB is 1985-03-15, "
                "SSN last four is 4472. What is the status of my denied healthcare claim from January?"
            )
        },
        config=config,
    )

    state = graph.invoke(
        {"user_message": "No, I don't want an email."},
        config=config,
    )

    assert state["email_consent"] is None
    assert state["email_offer_pending"] is True
    assert state["case_closed"] is False


def test_document_question_with_send_is_not_email_consent():
    config = {"configurable": {"thread_id": "test-send-document-question"}}
    graph.invoke(
        {
            "user_message": (
                "My name is Margaret Chen, DOB is 1985-03-15, "
                "SSN last four is 4472. What is the status of my denied healthcare claim from January?"
            )
        },
        config=config,
    )

    state = graph.invoke(
        {"user_message": "What documents should I send?"},
        config=config,
    )

    assert state["authorized_action"] == "read_document_guidance"
    assert state["email_consent"] is None
    assert "requested documents" in state["assistant_message"]

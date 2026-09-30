from claim_agent.tools import (
    get_claim_followup_guidance,
    get_claim_for_action,
    get_document_guidance_for_claim,
    send_email_summary,
)


def test_claim_tool_returns_only_action_scoped_fields():
    result = get_claim_for_action("P9", "CL-2048", "read_denial_reason")
    assert result is not None
    assert result["denial_reason"]
    # A denial answer may present the recorded next steps.
    assert result["documents_needed"] == ["pathology report", "office note"]
    assert result["appeal_deadline"] == "2026-03-18"
    assert "net_pay" not in result
    assert "allowed_max_amount" not in result


def test_document_tool_uses_claim_and_case_type_guidance():
    result = get_document_guidance_for_claim("P9", "CL-2048")
    assert result is not None
    assert "pathology report" in result["documents"]
    assert "medical claims" in result["case_type_guidance"].lower()


def test_followup_tool_applies_fixture_rule():
    result = get_claim_followup_guidance(
        "P9",
        "CL-2048",
        "document_submission",
        "processing_time_after_submission",
    )
    assert result is not None
    assert "usually less than a week" in result


def test_email_tool_requires_explicit_consent():
    result = send_email_summary(
        "P9",
        "CL-2048",
        "Claim summary",
        consent=False,
    )
    assert result["status"] == "not_sent"

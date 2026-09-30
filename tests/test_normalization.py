from claim_agent.guardrails.normalization import normalize_hints, normalize_pii, validate_pii_formats


def test_pii_is_stored_in_canonical_format():
    result = normalize_pii(
        {
            "name": "  Margaret   Chen ",
            "dob": "1985/3/15",
            "phone": "(650) 521-2836",
            "email": " Margaret@EMAIL.com ",
            "id_last4": "SSN 4472",
            "policy_number": "pol 9921",
            "id_type": " SSN_LAST4 ",
        }
    )

    assert result == {
        "name": "Margaret Chen",
        "dob": "1985-03-15",
        "phone": "+16505212836",
        "email": "margaret@email.com",
        "id_last4": "4472",
        "policy_number": "POL-9921",
        "id_type": "ssn_last4",
    }


def test_hints_are_normalized():
    assert normalize_hints(
        {"case_type": " Healthcare ", "status": " DENIED ", "month": "january"}
    ) == {"case_type": "healthcare", "status": "denied", "month": "January"}


def test_obviously_invalid_pii_is_rejected_before_verification():
    errors = validate_pii_formats(
        {"dob": "1985-99-40", "id_last4": "44", "email": "not-an-email"}
    )
    assert set(errors) == {"dob", "id_last4", "email"}

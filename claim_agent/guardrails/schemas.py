from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IdentityInputReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_input_only: bool
    source: Optional[str] = Field(description="Copy the entire current message only when it solely supplies identity information; otherwise null.")


class SecurityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk: Literal["low", "medium", "high"]
    category: Literal[
        "normal_customer_request",
        "data_exfiltration",
        "prompt_injection",
        "credential_request",
        "authorization_bypass",
        "unknown",
    ]
    reason: str = Field(min_length=1, max_length=300)
    declared_role: Literal["policyholder", "delegate", "unknown"] = Field(
        description="Role explicitly stated in the current message, including an answer to the last clarification; never proof of identity or authorization.",
    )
    clarification_topic: Literal["none", "ownership", "requested_access"] = Field(
        description="The unresolved access question for medium risk only; none for low or high risk.",
    )

    @model_validator(mode="after")
    def check_consistency(self):
        allowed = {
            "low": {"normal_customer_request"},
            "medium": {"unknown"},
            "high": {"data_exfiltration", "prompt_injection", "credential_request", "authorization_bypass"},
        }
        if self.category not in allowed[self.risk] or not self.reason.strip():
            raise ValueError("Invalid security assessment")
        if (self.risk == "medium") != (self.clarification_topic != "none"):
            raise ValueError("Clarification topic must match the risk decision")
        return self

"""Structured outputs for turn understanding and bounded case reasoning."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


BusinessIntent = Literal[
    "status_inquiry", "denial_question", "document_submission", "appeal_deadline",
    "next_steps", "general_claim_question", "payment_question", "claim_update", "document_upload",
]
INTENT_DOC = (
    'Business intent. payment_question: any amount paid, expected, allowed or owed, or the meaning of a monetary field such as net_fee, net_pay, allowed_max_amount (also when combined with another money question). status_inquiry: current claim state. denial_question: why a claim was denied. document_submission: which documents are needed, alternatives, how/when to send them. appeal_deadline: the appeal or filing deadline. next_steps: what to do next. claim_update: asking to change, correct or update claim or policy data (address, phone, bank details, coverage, filing an appeal for them). document_upload: asking the assistant itself to upload or attach files. general_claim_question ONLY when no more specific intent applies.'
)


class HintSources(BaseModel):
    """Literal current-message evidence for semantically normalized search hints."""
    model_config = ConfigDict(extra="forbid")
    case_type: Optional[str] = None
    status: Optional[str] = None
    month: Optional[str] = None


class ClaimQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: BusinessIntent = Field(description=INTENT_DOC)
    question: str = Field(min_length=1, max_length=800)
    source: Optional[str] = Field(default=None, description="Exact current-message clause asking this question. Never quote history or manufacture a question from an email reply.")
    case_id: Optional[str] = Field(default=None, description="Explicit current-message claim identifier CL-..., NEVER a POL-... policy identifier; null when absent.")
    case_type: Optional[str] = Field(default=None, description="Claim type explicitly described for this question, such as healthcare, dental, or auto; this is a caller-supplied search hint, not verified evidence.")
    status: Optional[str] = Field(default=None, description="Claim status explicitly described for this question, such as denied, open, or closed; preserve it as an unverified search hint.")
    month: Optional[str] = None
    year: Optional[str] = Field(default=None, description="Explicit claim year in this message; null if absent. Never use DOB, today's year, or a historical year.")
    hint_sources: HintSources = Field(default_factory=HintSources)


class RoleStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["policyholder", "delegate"]
    source: str = Field(min_length=1, max_length=500)


class CorrectionStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirm", "reject"]
    source: str = Field(min_length=1, max_length=500)


class IdTypeStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ssn_last4", "national_id_last4"]
    source: str = Field(min_length=1, max_length=100)


class IdentityExtraction(BaseModel):
    """Verbatim identity values from the current message, never normalized guesses."""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, description="Exact current-message substring containing the caller's name; null if absent.")
    dob: Optional[str] = Field(default=None, description="Exact current-message date-of-birth value, e.g. March 15, 1985; do not rewrite it to ISO format.")
    phone: Optional[str] = Field(default=None, description="Exact current-message phone value, preserving punctuation and any mistakes; null if absent.")
    email: Optional[str] = Field(default=None, description="Exact current-message email value, preserving any mistakes; null if absent.")
    id_last4: Optional[str] = Field(default=None, description="Exact current-message ID-last-four value; preserve wrong lengths for validation, never truncate or fill missing digits.")
    policy_number: Optional[str] = Field(default=None, description="Exact current-message caller policy number; null if absent.")
    represented_name: Optional[str] = Field(default=None, description="Exact current-message policyholder name when the caller is a delegate, not the delegate's name.")
    represented_policy_number: Optional[str] = Field(default=None, description="Exact current-message represented policyholder policy number; null if absent.")
    id_type: Optional[IdTypeStatement] = None
    caller_role: Optional[RoleStatement] = None
    identity_correction: Optional[CorrectionStatement] = None


class TurnUnderstanding(BaseModel):
    """History-aware business signals; this schema cannot write identity fields."""
    model_config = ConfigDict(extra="forbid")

    dialogue_act: Literal["claim_request", "email_reply", "identity_reply", "case_clarification", "conversation"] = Field(
        default="conversation", description="Classify this message first. Yes/no about a pending email offer is email_reply, not document submission. A mixed email reply plus a new claim question is claim_request.",
    )
    case_type: Optional[str] = Field(default=None, description="Claim type explicitly mentioned in the current message, such as healthcare, dental, or auto; null if absent, never copied from history.")
    status: Optional[str] = Field(default=None, description="Claim status explicitly mentioned in the current message, such as denied, open, or closed; null if absent, never copied from history.")
    month: Optional[str] = None
    year: Optional[str] = Field(default=None, description="Explicit claim year in CURRENT message only; never infer one or use the DOB year.")
    case_id: Optional[str] = Field(default=None, description="Explicit current-message CL-... claim identifier, never POL-...; null if absent.")
    hint_sources: HintSources = Field(default_factory=HintSources)
    new_case: bool = False
    document_alternatives_exhausted: Optional[bool] = Field(
        default=None,
        description="True when the caller already checked the provider/lab/clinic and can obtain neither the original nor any scan/copy/substitute, including 'it/them' follow-ups about previously discussed documents. False only if they now can obtain materials. Null for unrelated turns or just a missing original.",
    )
    requests: List[ClaimQuestion] = Field(default_factory=list, max_length=5)
    request_mode: Literal["append", "replace", "continue", "cancel"] = "append"

    intent: Literal[
        "status_inquiry",
        "denial_question",
        "document_submission",
        "appeal_deadline",
        "next_steps",
        "general_claim_question",
        "payment_question",
        "representative_request",
        "claim_update",
        "document_upload",
        "unknown",
    ] = Field(default="unknown", description=INTENT_DOC + " representative_request: the caller asks for a human. unknown: identity-only, email-only or no business request.")

    needs_human_support: bool = False

    scope: Literal["in_scope", "out_of_scope", "uncertain"] = Field(
        default="uncertain",
        description="Domain relevance, not intent completeness: identity answers and own-policy/delegate clarifications are in_scope even with unknown intent.",
    )
    emotion: Literal[
        "neutral",
        "frustrated",
        "anxious",
        "angry",
        "confused",
        "refusing",
    ] = "neutral"


class TurnExtraction(TurnUnderstanding):
    """Internal merge of business understanding and source-checked identity data."""
    name: Optional[str] = None
    dob: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    id_last4: Optional[str] = None
    id_type: Optional[str] = None
    policy_number: Optional[str] = None
    caller_role: Optional[Literal["policyholder", "delegate"]] = None
    represented_name: Optional[str] = None
    represented_policy_number: Optional[str] = None
    identity_correction: Literal["none", "confirm", "reject"] = "none"


CaseTool = Literal["get_claim_for_action", "get_document_guidance_for_claim", "get_claim_followup_guidance", "get_claim_field_definitions"]
FollowupTopic = Literal[
    "missing_required_material_alternatives", "submission_timing",
    "processing_time_after_submission", "submission_method",
    "file_format_requirements", "receipt_confirmation",
]


class CasePlan(BaseModel):
    """No identity, file path, recipient or arbitrary tool arguments."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["execute", "clarify", "human"]
    tools: List[CaseTool]
    followup_topic: Optional[FollowupTopic]


class CaseReadOptions(BaseModel):
    """Model selects optional capabilities; code constructs the tool sequence."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["execute", "clarify", "human"]
    document_guidance: bool = Field(description="Need document requirements, replacement options, or manual-review guidance? False if not needed or unavailable.")
    followup_topic: Optional[FollowupTopic] = Field(description="Only a follow-up topic actually asked about now. Null for a plain status/denial question. The harness adds its tool automatically.")


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    sources: List[CaseTool]


class AnswerReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported_by_evidence: bool
    answers_question: bool
    no_unauthorized_actions: bool


class VerificationReply(BaseModel):
    """Phrasing only: the verification decision and every fact come from code."""
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=900)

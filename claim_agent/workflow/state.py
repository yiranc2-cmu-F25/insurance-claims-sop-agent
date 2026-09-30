from typing import Annotated, Any, Dict, List, Optional, TypedDict
from ..services.memory_policy import bounded_messages


class ClaimsState(TypedDict, total=False):
    user_message: str
    input_block_reason: str
    pending_identity_changes: Dict[str, str]
    turn_identity_supplied: bool
    identity_expired: bool
    verified_at: Optional[float]
    verified_last_active_at: Optional[float]
    verification_failed_attempts: int
    verification_locked_until: float
    pending_requests: List[Dict[str, Any]]
    completed_requests: List[Dict[str, str]]
    queue_continue: bool
    turn_has_requests: bool
    assistant_message: str
    messages: Annotated[List[Dict[str, Any]], bounded_messages]
    case_memories: Dict[str, Any]

    phase: str
    collected_pii: Dict[str, str]
    caller_role: str
    represented_customer: Dict[str, str]
    verified_caller_id: Optional[str]
    turn_case_hint: bool
    document_alternatives_exhausted: bool
    pii_errors: Dict[str, str]
    pii_conflicts: Dict[str, str]
    security_risk: str
    security_status: str
    security_clarification_pending: bool
    security_clarification_topic: str
    security_clarification_attempts: int
    security_declared_role: str
    security_category: str
    security_reasons: List[str]
    intent_hint: Dict[str, str]
    hint_schema_version: int
    turn_intent: str
    llm_available: bool
    llm_error: bool
    verified_party_id: Optional[str]
    verification_matches: List[str]
    selected_claim_id: Optional[str]
    requested_intent: str
    requested_question: str
    resolved_intent: Optional[str]
    authorized_action: Optional[str]
    authorization_denied: bool
    grounded_claim: Optional[Dict[str, Any]]
    harness_status: str
    case_tool_calls: int
    audit_events: List[Dict[str, Any]]
    discussed_answers: List[str]
    discussed_claim_id: Optional[str]
    email_offer_pending: bool
    email_offer: Optional[Dict[str, Any]]
    email_result: Optional[Dict[str, str]]
    email_delivery: Optional[Dict[str, Any]]

    emotion: str
    distress_turns: int
    dialogue_act: str
    session_identity: List[str]
    new_conversation_suggested: bool
    needs_human_support: bool
    handoff_status: str
    handoff_reason: Optional[str]
    handoff_request_id: Optional[str]
    handoff_created_at: Optional[str]
    handoff_summary: Optional[Dict[str, Any]]
    scope: str
    off_topic_attempts: int
    refusal_attempts: int
    email_consent: Optional[str]
    case_closed: bool

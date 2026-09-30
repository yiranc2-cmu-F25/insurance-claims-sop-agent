"""Business permissions, independent of language interpretation."""

ALLOWED_ACTIONS = {
    "status_inquiry": "read_claim_status",
    "denial_question": "read_denial_reason",
    "document_submission": "read_document_guidance",
    "appeal_deadline": "read_appeal_deadline",
    "next_steps": "read_next_steps",
    "general_claim_question": "read_claim_summary",
    "payment_question": "read_claim_amounts",
}
DISALLOWED_INTENTS = {"claim_update": "modify_claim", "document_upload": "upload_document", "new_claim": "file_new_claim"}
DISALLOWED_LABELS = {"modify_claim": "changes to a claim or policy", "upload_document": "document uploads",
                     "file_new_claim": "filing a new claim"}
ACTION_TOOLS = {action: {"get_claim_for_action"} for action in ALLOWED_ACTIONS.values()}
for action in ("read_document_guidance", "read_next_steps", "read_denial_reason", "read_claim_summary", "read_claim_status"):
    ACTION_TOOLS[action] |= {"get_document_guidance_for_claim", "get_claim_followup_guidance"}
ACTION_TOOLS["read_appeal_deadline"].add("get_claim_followup_guidance")
ACTION_TOOLS["read_claim_amounts"].add("get_claim_field_definitions")
MAX_CASE_TOOL_CALLS = 3

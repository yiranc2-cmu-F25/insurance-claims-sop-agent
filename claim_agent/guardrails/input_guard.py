import re
import unicodedata
from typing import Dict


def sanitize_user_text(text: str) -> str:
    """Normalize Unicode and remove invisible/control characters."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", text)
    text = "".join(
        ch for ch in text
        if ch in "\n\t" or not unicodedata.category(ch).startswith("C")
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def detect_sensitive_overcollection(text: str) -> Dict[str, str]:
    checks = [
        (
            "full_ssn",
            r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b",
            "Please do not send a full SSN. Only provide the last four digits.",
        ),
        (
            "api_key",
            r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b",
            "Please do not send API keys or access tokens in chat.",
        ),
        (
            "bearer_token",
            r"\bBearer\s+[A-Za-z0-9._-]{16,}\b",
            "Please do not send passwords or access tokens in chat.",
        ),
    ]
    for category, pattern, message in checks:
        if re.search(pattern, text, re.IGNORECASE):
            return {"category": category, "message": message}
    return {}


def prepare_message(text: str) -> Dict[str, str]:
    """HTTP boundary: remove prohibited input before entering the checkpointed graph."""
    cleaned = sanitize_user_text(text)
    sensitive = detect_sensitive_overcollection(cleaned)
    return {
        "user_message": "" if sensitive else cleaned,
        "input_block_reason": sensitive.get("category", ""),
    }


def input_guard(state):
    text = sanitize_user_text(state.get("user_message", ""))
    per_turn = {
        "security_status": "pending", "security_risk": "unknown",
        "security_category": "unknown", "security_reasons": [],
        "llm_error": False, "case_tool_calls": 0, "harness_status": "",
        "authorized_action": None, "resolved_intent": None,
        "authorization_denied": False,
    }
    blocked_replies = {
        "full_ssn": "Please do not send a full SSN. Only provide the last four digits.",
        "api_key": "Please do not send API keys or access tokens in chat.",
        "bearer_token": "Please do not send passwords or access tokens in chat.",
    }
    if not text and state.get("input_block_reason") in blocked_replies:
        reply = blocked_replies[state["input_block_reason"]]
        return {**per_turn, "user_message": "", "assistant_message": reply,
                "messages": state.get("messages", []) + [
                    {"role": "user", "content": "[sensitive input withheld]"},
                    {"role": "assistant", "content": reply},
                ]}
    if not text:
        reply = "Please enter a message so I can help with your insurance question."
        return {
            **per_turn,
            "user_message": text,
            "input_block_reason": "empty",
            "assistant_message": reply,
            "messages": state.get("messages", []) + [{"role": "assistant", "content": reply}],
        }

    sensitive = detect_sensitive_overcollection(text)
    if sensitive:
        reply = sensitive["message"]
        return {
            **per_turn,
            # Do not retain the sensitive raw message in graph state.
            "user_message": "",
            "input_block_reason": sensitive["category"],
            "assistant_message": reply,
            "messages": state.get("messages", []) + [
                {"role": "user", "content": "[sensitive input withheld]"},
                {"role": "assistant", "content": reply},
            ],
        }

    return {
        **per_turn,
        "user_message": text,
        "input_block_reason": "",
        "assistant_message": "",
    }

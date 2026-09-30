"""Small helpers shared by workflow nodes."""

from typing import Dict, List

from .state import ClaimsState


def append_message(state: ClaimsState, content: str) -> List[Dict[str, str]]:
    return state.get("messages", []) + [{"role": "assistant", "content": content}]

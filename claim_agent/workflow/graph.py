"""Workflow entry point: inspect this file first to see the phase order."""

from langgraph.graph import END, START, StateGraph

from ..guardrails.input_guard import input_guard
from ..services.handoff import handoff_gate, pause_gate
from ..services.checkpoints import create_checkpointer
from ..services.verification_session import session_guard
from .gates import format_gate, scope_gate, security_gate
from .intake import capture_turn
from .post_process import post_process_node
from .process_case import process_node
from .resolve_intent import authorization_node, resolve_node
from .state import ClaimsState
from .verify_id import verify_node


def _scope_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else state.get("phase", "VERIFY_ID")


def _format_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else "continue"


def _security_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else "continue"


def _input_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else "continue"


def _verify_route(state: ClaimsState) -> str:
    return "resolve" if state.get("verified_party_id") else "stop"


def _resolve_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else ("authorize" if state.get("selected_claim_id") else "stop")


def _post_process_route(state: ClaimsState) -> str:
    return "resolve" if state.get("phase") == "RESOLVE_INTENT" and not state.get("assistant_message") else "stop"


def _authorization_route(state: ClaimsState) -> str:
    return "stop" if state.get("assistant_message") else "process"


builder = StateGraph(ClaimsState)
builder.add_node("pause_gate", pause_gate)
builder.add_node("session_guard", session_guard)
builder.add_node("handoff_gate", handoff_gate)
builder.add_node("input_guard", input_guard)
builder.add_node("security_gate", security_gate)
builder.add_node("capture_turn", capture_turn)
builder.add_node("format_gate", format_gate)
builder.add_node("scope_gate", scope_gate)
builder.add_node("verify", verify_node)
builder.add_node("resolve", resolve_node)
builder.add_node("authorize", authorization_node)
builder.add_node("process", process_node)
builder.add_node("post_process", post_process_node)

builder.add_edge(START, "pause_gate")
builder.add_conditional_edges("pause_gate", _input_route, {"continue": "session_guard", "stop": END})
builder.add_conditional_edges("session_guard", _input_route, {"continue": "input_guard", "stop": END})
builder.add_conditional_edges("input_guard", _input_route, {"continue": "security_gate", "stop": END})
builder.add_conditional_edges("security_gate", _security_route, {"continue": "capture_turn", "stop": END})
builder.add_conditional_edges("capture_turn", _input_route, {"continue": "handoff_gate", "stop": END})
builder.add_conditional_edges("handoff_gate", _input_route, {"continue": "format_gate", "stop": END})
builder.add_conditional_edges("format_gate", _format_route, {"continue": "scope_gate", "stop": END})
builder.add_conditional_edges(
    "scope_gate",
    _scope_route,
    {
        "VERIFY_ID": "verify",
        "RESOLVE_INTENT": "resolve",
        "PROCESS_CASE": "resolve",
        "POST_PROCESS": "post_process",
        "stop": END,
    },
)
builder.add_conditional_edges("verify", _verify_route, {"resolve": "resolve", "stop": END})
builder.add_conditional_edges("resolve", _resolve_route, {"authorize": "authorize", "stop": END})
builder.add_conditional_edges("authorize", _authorization_route, {"process": "process", "stop": END})
builder.add_edge("process", END)
builder.add_conditional_edges("post_process", _post_process_route, {"resolve": "resolve", "stop": END})

graph = builder.compile(checkpointer=create_checkpointer())

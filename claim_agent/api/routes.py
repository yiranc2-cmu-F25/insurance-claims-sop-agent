"""HTTP handlers delegate workflow and side effects to their owning modules."""

from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Response

from ..workflow.graph import graph
from ..llm.client import get_llm_status
from ..services.handoff import PAUSED_REPLY, request_handoff, resume_bot
from ..services.email_followup import choose_email, poll_email_delivery
from ..services.verification_session import expire_identity
from ..guardrails.input_guard import prepare_message
from .presenters import conversation_payload
from .schemas import ChatRequest, EmailChoiceRequest, EmailStatusRequest
from .sessions import SESSION_COOKIE, session_context

router = APIRouter(prefix="/api")


def current_state(config):
    """Enforce expiration on reads and button endpoints, not only chat turns."""
    state = graph.get_state(config).values
    update = expire_identity(state)
    if update:
        graph.update_state(config, update, as_node="pause_gate")
    return {**state, **update}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/llm-status")
def llm_status():
    return {"status": get_llm_status()}


@router.post("/session")
def create_session(response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    """Create an opaque browser session without requiring login."""
    session_context(response, session_cookie)
    return {"status": "ok"}


@router.get("/conversation")
def conversation(response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        return conversation_payload(current_state(config))


@router.delete("/conversation")
def forget_conversation(response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    """Delete only the current browser session; never accept a client-supplied target ID."""
    config, lock = session_context(response, session_cookie)
    with lock:
        graph.checkpointer.delete_thread(config["configurable"]["thread_id"])
        session_context(response, None)  # Rotate cookie; old offers/identity are unusable.
        return conversation_payload({"assistant_message": "Your saved conversation has been cleared. Please verify your identity again before accessing claim details."})


@router.post("/chat")
def chat(request: ChatRequest, response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        previous = current_state(config)
        if previous.get("handoff_status") == "requested":
            # Do not invoke the graph, store the new text, or call the model.
            response.status_code = 409
            return conversation_payload({**previous, "assistant_message": PAUSED_REPLY})
        state = graph.invoke(prepare_message(request.message), config=config)
        if state.get("llm_error"):
            response.status_code = 503
        return conversation_payload(state)


@router.post("/handoff")
def create_handoff(response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        state = current_state(config)
        try:
            update = request_handoff(state)
        except ValueError:
            raise HTTPException(status_code=409, detail="No transfer has been offered in this session.") from None
        if update:
            graph.update_state(config, update, as_node="pause_gate")
        return conversation_payload({**state, **update})


@router.post("/email-choice")
def email_choice(request: EmailChoiceRequest, response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        state = current_state(config)
        try:
            update = choose_email(state, request.offer_id, request.choice)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if update:
            graph.update_state(config, update, as_node="post_process")
        result = {**state, **update}
        # On retries return the original decision receipt, not a later chat reply.
        result["assistant_message"] = result["email_result"]["reply"]
        return conversation_payload(result)


@router.post("/handoff/resume")
def return_to_bot(response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        state = current_state(config)
        update = resume_bot(state)
        if update:
            graph.update_state(config, update, as_node="pause_gate")
        return conversation_payload({**state, **update})


@router.post("/email-status")
def email_status(request: EmailStatusRequest, response: Response, session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    config, lock = session_context(response, session_cookie)
    with lock:
        state = current_state(config)
        try:
            update = poll_email_delivery(state, request.offer_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if update:
            graph.update_state(config, update, as_node="post_process")
        return conversation_payload({**state, **update})

import json
import os
from functools import lru_cache
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from .schemas import AnswerReview, CasePlan, CaseReadOptions, GroundedAnswer, IdentityExtraction, TurnExtraction, TurnUnderstanding, VerificationReply
from ..guardrails.identity_source import IDENTITY_FIELDS, source_checked_identity
from ..guardrails.business_source import source_checked_business
from . import prompts


load_dotenv()
_llm_runtime_status = "unknown"


class LLMUnavailable(RuntimeError):
    """Missing model, failed call or unusable response; contains no secrets."""


def get_llm_status() -> str:
    if not os.getenv("MODEL_API_KEY", "").strip():
        return "unavailable"
    return "configured" if _llm_runtime_status == "unknown" else _llm_runtime_status


@lru_cache(maxsize=1)
def get_llm() -> Optional[ChatOpenAI]:
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key:
        return None
    kwargs: Dict[str, Any] = {
        "model": os.getenv("MODEL_NAME") or "gpt-4o-mini",
        "api_key": api_key,
        "temperature": 0,
        "timeout": 12,
        "max_retries": 0,
    }
    if os.getenv("MODEL_BASE_URL"):
        kwargs["base_url"] = os.environ["MODEL_BASE_URL"]
    return ChatOpenAI(**kwargs)


def structured_call(schema, instructions: str, payload: Dict[str, Any], *, best_effort: bool = False):
    """best_effort calls (pure phrasing) may fail without marking the service unavailable."""
    global _llm_runtime_status
    try:
        model = get_llm()
        if model is None:
            raise LLMUnavailable("LLM unavailable")
        result = model.with_structured_output(
            schema, method="json_schema", strict=True,
        ).invoke([
            ("system", instructions),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ])
        parsed = result if isinstance(result, schema) else schema.model_validate(result)
    except Exception:
        if not best_effort:
            _llm_runtime_status = "unavailable"
        raise LLMUnavailable("LLM unavailable") from None
    _llm_runtime_status = "available"
    return parsed


def extract_turn_with_llm(text: str, *, context: Optional[Dict[str, Any]] = None) -> TurnExtraction:
    context = context or {}
    # Never pass transcripts, stored PII, case notes or pending values to this call.
    pending = [field for field in context.get("pending_identity_fields", [])
               if field in set(IDENTITY_FIELDS) | {"id_type"}]
    role = context.get("caller_role", "unknown")
    identity = structured_call(IdentityExtraction, prompts.EXTRACT_IDENTITY, {
        "message": text,
        "context": {
            "caller_role": role if role in {"policyholder", "delegate"} else "unknown",
            "pending_identity_fields": pending,
        },
    })
    updates = source_checked_identity(text, identity, pending_fields=pending)
    # The history-aware model has no identity/correction fields in its schema.
    understanding = structured_call(TurnUnderstanding, prompts.EXTRACT_TURN, {
        "message": text, "context": context,
    })
    return TurnExtraction(**source_checked_business(text, understanding, identity), **updates)


def plan_case_with_llm(question: str, intent: str, allowed_tools) -> CasePlan:
    options = structured_call(CaseReadOptions, prompts.PLAN_CASE, {
        "question": question, "intent": intent, "allowed_tools": sorted(allowed_tools),
    })
    if options.decision != "execute":
        # A stop decision never executes optional capabilities, even if supplied.
        return CasePlan(decision=options.decision, tools=[], followup_topic=None)
    tools = []
    topic = None
    if options.document_guidance and "get_document_guidance_for_claim" in allowed_tools:
        tools.append("get_document_guidance_for_claim")
    if options.followup_topic and "get_claim_followup_guidance" in allowed_tools:
        tools.append("get_claim_followup_guidance")
        topic = options.followup_topic
    return CasePlan(decision=options.decision, tools=tools, followup_topic=topic)


def compose_case_answer(question: str, intent: str, evidence: Dict[str, Any], emotion: str = "neutral") -> GroundedAnswer:
    return structured_call(GroundedAnswer, prompts.COMPOSE_ANSWER, {
        "question": question, "intent": intent, "evidence": evidence, "emotion": emotion,
    })


def compose_verification_reply(facts: Dict[str, Any]) -> VerificationReply:
    """Words only: facts, options and the verification decision are fixed by code."""
    return structured_call(VerificationReply, prompts.COMPOSE_VERIFICATION_REPLY, {"facts": facts}, best_effort=True)


def review_case_answer(question: str, answer: str, evidence: Dict[str, Any]) -> AnswerReview:
    return structured_call(AnswerReview, prompts.REVIEW_ANSWER, {"question": question, "answer": answer, "evidence": evidence})

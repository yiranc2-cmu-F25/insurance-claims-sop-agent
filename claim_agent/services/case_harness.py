"""Bounded, read-only PROCESS_CASE execution. All credentials come from state."""
import json
import logging
import re
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from .. import tools
from ..llm import client as llm
from ..llm.schemas import AnswerReview, CasePlan, GroundedAnswer
from ..guardrails.policy import ACTION_TOOLS, ALLOWED_ACTIONS, MAX_CASE_TOOL_CALLS
from ..guardrails.access import has_access
from ..guardrails.security import security_allows_actions
from ..guardrails.normalization import normalize_date


logger = logging.getLogger(__name__)
MAX_EVIDENCE_CHARS = 20000
MAX_ANSWER_CHARS = 4000
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
DATE_PHRASE = re.compile(
    rf"\b(?:(?:{_MONTHS})\.? \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}|\d{{1,2}}(?:st|nd|rd|th)? (?:{_MONTHS})\.? \d{{4}})\b", re.I,
)


def _canonical_dates(text):
    """Spell a recorded date any way, but it must still be the recorded date."""
    return DATE_PHRASE.sub(lambda m: normalize_date(m.group(0).replace(".", "")) or m.group(0), text)


class HarnessBlocked(Exception):
    def __init__(self, code):
        self.code = code


def _required_tools(action):
    """Fixed SOP prerequisites for an action; never left to the model."""
    required = ["get_claim_for_action"]
    if action == "read_claim_amounts":
        required.append("get_claim_field_definitions")
    if action == "read_document_guidance":
        required.append("get_document_guidance_for_claim")
    return required


def _prepare_plan(plan, allowed, action):
    if plan.decision != "execute":
        if plan.tools or plan.followup_topic:
            raise HarnessBlocked("invalid_plan")
        raise HarnessBlocked("clarification_needed" if plan.decision == "clarify" else "human_required")
    if len(plan.tools) > MAX_CASE_TOOL_CALLS:
        raise HarnessBlocked("tool_budget_exceeded")
    if len(set(plan.tools)) != len(plan.tools):
        raise HarnessBlocked("duplicate_tool")
    if not set(plan.tools) <= allowed:
        raise HarnessBlocked("tool_not_allowed")
    if bool(plan.followup_topic) != ("get_claim_followup_guidance" in plan.tools):
        raise HarnessBlocked("invalid_plan")
    # Fixed SOP prerequisites belong to the harness, not the model's discretion.
    required = _required_tools(action)
    ordered = required + [tool for tool in plan.tools if tool not in required]
    if not set(ordered) <= allowed:
        raise HarnessBlocked("tool_not_allowed")
    if len(ordered) > MAX_CASE_TOOL_CALLS:
        raise HarnessBlocked("tool_budget_exceeded")
    return plan.model_copy(update={"tools": ordered})


def _validate_answer(answer, evidence, claim_id):
    if not answer.answer.strip() or len(answer.answer) > MAX_ANSWER_CHARS:
        raise HarnessBlocked("output_blocked")
    # A listed tool that produced no evidence is noise, but some real evidence must be cited.
    if not set(answer.sources) & set(evidence):
        raise HarnessBlocked("output_blocked")
    # Mechanical checks supplement, rather than replace, the semantic review.
    if set(re.findall(r"\bCL-\d+\b", answer.answer, re.I)) - {claim_id}:
        raise HarnessBlocked("output_blocked")
    serialized = json.dumps(evidence, ensure_ascii=False)
    # A deadline can be discussed only when the evidence records or mentions one.
    if re.search(r"\bdeadline", answer.answer, re.I) and not re.search(r"deadline", serialized, re.I):
        raise HarnessBlocked("output_blocked")
    tokens = r"\b\d+(?:[.,/-]\d+)*\b"
    # Markdown list ordinals are formatting, not amounts, dates or claim facts.
    factual_text = _canonical_dates(re.sub(r"(?m)^[ \t]{0,3}\d{1,3}[.)][ \t]+", "", answer.answer))
    if set(re.findall(tokens, factual_text)) - set(re.findall(tokens, serialized)):
        raise HarnessBlocked("output_blocked")


def run_case(state, position=1, total=1, other_parts=()):
    """One plan, <=3 reads, one answer, one review; no autonomous retry loop.

    position/total describe where this answer sits in a multi-question reply so the
    wording reads as one message; they never change what is read or checked.
    """
    request_id = uuid4().hex
    audit = []
    count = 0
    evidence = {}
    action = state.get("authorized_action")
    intent = state.get("resolved_intent")
    question = state.get("requested_question") or state.get("user_message", "")

    def record(event, outcome, tool=None, elapsed_ms=None):
        item = {
            "request_id": request_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "outcome": outcome,
        }
        if action in ACTION_TOOLS:
            item["action"] = action
        if tool:
            item["tool"] = tool
        if elapsed_ms is not None:
            item["elapsed_ms"] = elapsed_ms
        # Deliberately exclude prompts, answers, PII, claim IDs and exception text.
        audit.append(item)
        logger.info("case_harness %s", json.dumps(item))

    def result(status, reply, **extra):
        return {
            "harness_status": status, "reply": reply,
            "case_tool_calls": count, "audit_events": audit, **extra,
        }

    try:
        if state.get("phase") != "PROCESS_CASE":
            raise HarnessBlocked("invalid_phase")
        if not state.get("llm_available"):
            raise llm.LLMUnavailable()
        party = state.get("verified_party_id")
        claim_id = state.get("selected_claim_id")
        if not party or len(set(state.get("verification_matches", [])) & {
            "name", "dob", "phone", "email", "id_last4"
        }) < 3:
            raise HarnessBlocked("identity_required")
        if (not claim_id or action not in ACTION_TOOLS
                or ALLOWED_ACTIONS.get(intent) != action
                or state.get("authorization_denied")
                or state.get("scope") != "in_scope"
                or not security_allows_actions(state)):
            raise HarnessBlocked("authorization_denied")
        if not has_access(state, action, claim_id):
            raise HarnessBlocked("authorization_denied")

        if (state.get("document_alternatives_exhausted")
                and action in {"read_document_guidance", "read_next_steps"}):
            # Manual-review recovery is a fixed SOP branch, after the same access checks.
            plan = CasePlan(decision="execute", tools=["get_document_guidance_for_claim"], followup_topic=None)
        elif not (ACTION_TOOLS[action] - set(_required_tools(action))):
            # Nothing optional to choose for this action: no planning call is needed.
            plan = CasePlan(decision="execute", tools=[], followup_topic=None)
        else:
            plan = CasePlan.model_validate(llm.plan_case_with_llm(
                question, intent, ACTION_TOOLS[action],
            ))
        plan = _prepare_plan(plan, ACTION_TOOLS[action], action)
        if (state.get("document_alternatives_exhausted")
                and action in {"read_document_guidance", "read_next_steps"}
                and "get_document_guidance_for_claim" not in plan.tools):
            raise HarnessBlocked("human_required")
        record("plan", "approved")

        for tool in plan.tools:
            if not has_access(state, action, claim_id):
                raise HarnessBlocked("authorization_denied")
            if count >= MAX_CASE_TOOL_CALLS:
                raise HarnessBlocked("tool_budget_exceeded")
            count += 1
            started = monotonic()
            try:
                if tool == "get_claim_for_action":
                    value = tools.get_claim_for_action(party, claim_id, action)
                    if value is None:
                        raise HarnessBlocked("claim_not_accessible")
                    if not isinstance(value, dict) or value.get("case_id") != claim_id:
                        raise HarnessBlocked("invalid_tool_result")
                    # Defense in depth if the adapter returns too many fields.
                    value = {k: v for k, v in value.items() if k in tools.ACTION_FIELDS[action]}
                    if not isinstance(value.get("status"), str):
                        raise HarnessBlocked("invalid_tool_result")
                elif tool == "get_document_guidance_for_claim":
                    value = tools.get_document_guidance_for_claim(party, claim_id)
                    if not isinstance(value, dict) or value.get("case_id") != claim_id:
                        raise HarnessBlocked("invalid_tool_result")
                    value = {k: v for k, v in value.items() if k in {
                        "case_id", "case_type", "default", "case_type_guidance", "documents",
                        "alternatives", "default_alternative", "human_review"
                    }}
                elif tool == "get_claim_field_definitions":
                    value = tools.get_claim_field_definitions(party, claim_id)
                    if not isinstance(value, dict) or value.get("case_id") != claim_id:
                        raise HarnessBlocked("invalid_tool_result")
                    value = {k: v for k, v in value.items() if k in {"case_id", "currency", "fields", "limitations"}}
                elif tool == "get_claim_followup_guidance":
                    value = tools.get_claim_followup_guidance(party, claim_id, intent, plan.followup_topic)
                    if value is not None and not isinstance(value, str):
                        raise HarnessBlocked("invalid_tool_result")
                else:
                    raise HarnessBlocked("tool_not_allowed")
                evidence[tool] = value
                if len(json.dumps(evidence, ensure_ascii=False)) > MAX_EVIDENCE_CHARS:
                    raise HarnessBlocked("evidence_too_large")
            except HarnessBlocked as exc:
                record("tool", exc.code, tool, round((monotonic() - started) * 1000))
                raise
            except Exception:
                record("tool", "failed", tool, round((monotonic() - started) * 1000))
                raise HarnessBlocked("tool_failed") from None
            record("tool", "ok", tool, round((monotonic() - started) * 1000))

        answer = GroundedAnswer.model_validate(llm.compose_case_answer(
            question, intent, evidence, state.get("emotion", "neutral"), position, total, other_parts,
        ))
        _validate_answer(answer, evidence, claim_id)
        review = AnswerReview.model_validate(llm.review_case_answer(
            question, answer.answer, evidence,
        ))
        if not all((review.supported_by_evidence, review.answers_question, review.no_unauthorized_actions)):
            raise HarnessBlocked("output_blocked")
        record("output", "approved")
        if not has_access(state, action, claim_id):
            raise HarnessBlocked("authorization_denied")
        doc_evidence = evidence.get("get_document_guidance_for_claim") or {}
        return result("completed", answer.answer.strip(), grounded_claim=evidence["get_claim_for_action"],
                      documents_exhausted=bool(state.get("document_alternatives_exhausted")
                                               and doc_evidence.get("documents") and doc_evidence.get("human_review")))
    except llm.LLMUnavailable:
        record("stop", "llm_unavailable")
        return result("llm_unavailable", "The language model is unavailable. Please try again later; no email has been sent.")
    except HarnessBlocked as exc:
        record("stop", exc.code)
        reply = (
            "I couldn't match that to a specific question about this claim. I can help with claim status, "
            "the denial reason, required documents, the appeal deadline, payment amounts, or next steps. "
            "If your question isn't about your insurance claim, I'm not able to help with it here."
            if exc.code == "clarification_needed"
            else "I couldn't safely complete this request. Please contact a human claims representative."
        )
        return result(exc.code, reply)
    except Exception:
        record("stop", "invalid_model_output")
        return result("invalid_model_output", "I couldn't safely complete this request. Please contact a human claims representative.")

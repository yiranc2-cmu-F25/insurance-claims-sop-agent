"""Explicit, session-scoped email decisions; chat/LLM output cannot send mail."""
from uuid import uuid4
from time import time

from .handoff import offer_handoff
from ..tools import get_claim_for_action, get_policyholder, send_email_summary, get_consent_status
from ..guardrails.access import has_access
from ..guardrails.security import security_allows_actions


EMAIL_PROMPT = (
    'Would you like an email summary? Please click "Yes, send summary" or '
    '"No, skip" below. You can also keep asking claim questions.'
)
IDENTITY_FIELDS = {"name", "dob", "phone", "email", "id_last4"}
MAX_APPROVAL_POLLS = 5
APPROVAL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 1


def mask_email(address):
    if not address or "@" not in address:
        return None
    local, domain = address.split("@", 1)
    return local[:1] + "***@" + domain


def build_email_summary(state, answers, claim=None):
    """Status/outcome, what was discussed, and follow-up items; every part is server data."""
    claim = claim or state.get("grounded_claim") or {}
    claim_id = state["selected_claim_id"]
    party = state["verified_party_id"]
    header = f"Claim {claim_id}"
    if claim.get("case_type"):
        header += f" ({claim['case_type']})"
    if claim.get("status"):
        header += f" — status: {claim['status']}"
    lines = [header]
    if claim.get("summary"):
        lines.append(f"Outcome: {claim['summary']}")
    lines += ["", "What we discussed:"]
    lines += [f"- {answer.strip().replace(chr(10), chr(10) + '  ')}" for answer in answers if answer.strip()] or [
        "- No validated answers were recorded."
    ]
    # Follow-up items come from permission-checked reads, never from the model.
    steps = []
    permitted = False
    if has_access(state, "read_next_steps", claim_id):
        permitted = True
        record = get_claim_for_action(party, claim_id, "read_next_steps") or {}
        if record.get("documents_needed"):
            steps.append("Documents still needed: " + ", ".join(record["documents_needed"]))
    if has_access(state, "read_appeal_deadline", claim_id):
        permitted = True
        record = get_claim_for_action(party, claim_id, "read_appeal_deadline") or {}
        if record.get("appeal_deadline"):
            steps.append(f"Appeal deadline on file: {record['appeal_deadline']}")
    lines += ["", "Follow-up items / next steps:"]
    if steps:
        lines += [f"- {step}" for step in steps]
    elif permitted:
        lines.append("- No open follow-up items are recorded for this claim.")
    else:
        lines.append("- Follow-up details are not available under the current authorization.")
    return "\n".join(lines)


def new_email_offer(state, answers, claim=None):
    """Bind a button to the exact verified case and validated summary offered."""
    policyholder = get_policyholder(state["verified_party_id"]) or {}
    return {
        "id": uuid4().hex,
        "party_id": state["verified_party_id"],
        "claim_id": state["selected_claim_id"],
        "summary": build_email_summary(state, answers, claim),
        "recipient_masked": mask_email(policyholder.get("email")),
        "caller_id": state.get("verified_caller_id"),
        "caller_role": state.get("caller_role", "policyholder"),
    }


def _offer_is_current(state):
    offer = state.get("email_offer") or {}
    return bool(
        offer.get("id") and offer.get("summary")
        and state.get("phase") == "POST_PROCESS"
        and state.get("verified_party_id")
        and len(set(state.get("verification_matches", [])) & IDENTITY_FIELDS) >= 3
        and offer.get("party_id") == state.get("verified_party_id")
        and offer.get("claim_id") == state.get("selected_claim_id")
        and offer.get("caller_id") == state.get("verified_caller_id")
        and offer.get("caller_role", "policyholder") == state.get("caller_role", "policyholder")
    )


def _send_is_safe(state):
    return has_access(state, "send_email_summary", state.get("selected_claim_id")) and not (
        not security_allows_actions(state) or state.get("authorization_denied")
        or state.get("pii_errors") or state.get("pii_conflicts")
        or (state.get("email_delivery") or {}).get("status") == "pending"
    )


def public_email_offer(state):
    offer = state.get("email_offer") or {}
    pending = bool(state.get("email_offer_pending") and _offer_is_current(state))
    active = pending and state.get("handoff_status") != "requested"
    return {
        "id": offer.get("id"), "pending": pending,
        "can_send": active and _send_is_safe(state), "can_skip": active,
        # The verified caller may preview exactly what a click would send.
        "preview": offer.get("summary") if pending else None,
        "recipient_masked": offer.get("recipient_masked") if pending else None,
        "delivery_status": (state.get("email_result") or {}).get("status"),
        "simulated": True,
    }


def choose_email(state, offer_id, choice):
    """Call under the session lock. Repeated same-choice clicks are idempotent."""
    if choice not in {"send", "skip"}:
        raise ValueError("Choose send or skip.")
    if state.get("handoff_status") == "requested":
        raise ValueError("The bot is paused. Choose Return to bot first.")
    offer = state.get("email_offer") or {}
    if not _offer_is_current(state) or offer.get("id") != offer_id:
        raise ValueError("This email offer is no longer current. Please refresh the conversation.")

    consent = "accepted" if choice == "send" else "declined"
    if not state.get("email_offer_pending"):
        if state.get("email_consent") == consent and state.get("email_result"):
            return {}  # Return the saved result without sending again.
        raise ValueError("The email choice is already complete.")
    if choice == "send" and not _send_is_safe(state):
        raise ValueError("Please resolve the identity or safety issue before sending a summary.")

    handoff = {}
    job = None
    if choice == "skip":
        delivery_status = "not_sent"
        reply = "Understood. I will not send an email. You can ask another claim question."
    else:
        recipient = None
        try:
            delivery = send_email_summary(
                offer["party_id"], offer["claim_id"], offer["summary"], consent=True,
            )
            delivery_status = delivery.get("status")
            recipient = mask_email(delivery.get("recipient"))
            if delivery_status == "pending":
                now = time()
                job = {
                    "offer_id": offer_id, "party_id": offer["party_id"],
                    "claim_id": offer["claim_id"], "caller_id": state.get("verified_caller_id"),
                    "caller_role": state.get("caller_role", "policyholder"),
                    "scenario": delivery.get("scenario", "default"), "status": "pending",
                    "poll_count": 1, "started_at": now, "last_polled_at": now,
                }
        except Exception:
            delivery_status = "failed"
        on_file = f" ({recipient})" if recipient else ""
        if delivery_status == "approved":
            reply = f"Demo: the email summary was prepared for the verified email on file{on_file}. No real email was sent."
        elif delivery_status == "pending":
            reply = f"Demo: the email summary for the verified email on file{on_file} is pending delivery approval. No real email was sent."
        else:
            delivery_status = "failed"
            reply = "I could not prepare the email summary. You can use Transfer to human for help."
            handoff = offer_handoff("email_failure")

    if job:
        job["reply"] = reply
    return {
        **({"email_delivery": job} if choice == "send" else {}),
        **handoff, "email_consent": consent, "email_offer_pending": False,
        "email_result": {"status": delivery_status, "reply": reply},
        "case_closed": delivery_status != "pending", "assistant_message": reply,
        "messages": state.get("messages", []) + [
            {"role": "user", "content": "[Email button: " + choice + "]"},
            {"role": "assistant", "content": reply},
        ],
    }


def _delivery_is_authorized(state, job):
    return bool(
        state.get("handoff_status") != "requested"
        and job.get("party_id") == state.get("verified_party_id")
        and job.get("caller_id") == state.get("verified_caller_id")
        and job.get("caller_role") == state.get("caller_role", "policyholder")
        and has_access(state, "send_email_summary", job.get("claim_id"))
        and security_allows_actions(state)
        and not state.get("authorization_denied")
        and not state.get("pii_errors") and not state.get("pii_conflicts")
    )


def public_email_delivery(state):
    job = state.get("email_delivery") or {}
    return {
        "offer_id": job.get("offer_id"), "status": job.get("status"),
        "reply": job.get("reply", ""), "simulated": True,
        "can_poll": job.get("status") == "pending" and _delivery_is_authorized(state, job),
    }


def poll_email_delivery(state, offer_id):
    """Advance mock approval only; never repeat the send adapter. Run under session lock."""
    job = dict(state.get("email_delivery") or {})
    if not job or job.get("offer_id") != offer_id or not _delivery_is_authorized(state, job):
        raise ValueError("This delivery is unavailable or no longer authorized.")
    if job["status"] != "pending":
        return {}
    now = time()
    if now - job["started_at"] >= APPROVAL_TIMEOUT_SECONDS:
        status = "timeout"
    elif now - job["last_polled_at"] < POLL_INTERVAL_SECONDS:
        return {}
    else:
        try:
            status = get_consent_status(job["scenario"], job["poll_count"])
        except Exception:
            status = "failed"
        job["poll_count"] += 1
        if status == "pending" and job["poll_count"] >= MAX_APPROVAL_POLLS:
            status = "timeout"
    if status not in {"pending", "approved", "timeout"}:
        status = "failed"
    replies = {
        "pending": "Demo: the email summary is pending delivery approval. No real email was sent.",
        "approved": "Demo: approval completed and the summary was prepared for the customer's registered email. No real email was sent.",
        "timeout": "Demo: email approval timed out. No real email was sent. You can use Transfer to human for help.",
        "failed": "Demo: email approval failed. No real email was sent. You can use Transfer to human for help.",
    }
    job.update(status=status, reply=replies[status], last_polled_at=now)
    update = {"email_delivery": job}
    if status in {"failed", "timeout"}:
        update.update(offer_handoff("email_failure"))
    # A caller may continue chatting while approval is pending. Never overwrite
    # a newer offer, its consent or its last assistant message.
    if (state.get("email_offer") or {}).get("id") == offer_id:
        update.update(email_result={"status": status, "reply": replies[status]}, case_closed=status != "pending")
        if status != "pending":
            update.update(assistant_message=replies[status], messages=state.get("messages", []) + [
                {"role": "assistant", "content": replies[status]},
            ])
    return update

# SOP polish: scope backstop, emotional recovery, VERIFY_ID phrasing, email summary — 2026-09-30

Follows [SOP and memory repair](sop-memory-fix-2026-09-30.md). Live probes on the
previous build showed four gaps against the assignment: the spec's own off-topic
example ("What is RL?") was queued as a claim question after verification and
blocked the caller's next real question; the emotional-support example triggered
an immediate human-transfer offer instead of explain/persuade; VERIFY_ID replies
were fixed templates, so clarification questions ("why do you need my DOB?")
got canned answers; and the email summary was a concatenation of answers
without status/outcome or next steps.

## Changes

- **Request backlog.** An item the caller was asked to clarify (or told cannot
  proceed) is marked `awaiting_caller`. The next real question or an explicit
  "continue" supersedes it; a hints-only reply refreshes it. Within one turn a
  clarification no longer blocks the other questions asked in the same message.
  The harness clarification text now names the supported topics and says that
  non-claim questions cannot be helped with here.
- **Scope.** `EXTRACT_TURN` states that general-knowledge/AI questions are
  `out_of_scope` at every phase, including after a completed case. The two
  scope replies are more courteous; the second still offers a human.
- **Emotional escalation floor.** `handoff_gate` no longer pauses the workflow
  on `needs_human_support`. A transfer is offered by the phase reply when
  distress is sustained: two consecutive non-neutral turns with the model's
  recommendation, or three regardless (`distress_turns`, reset by a neutral turn,
  a successful verification or a delivered answer). Two refusals, explicit
  requests and blocked workflows escalate as before. A delivered answer or a
  successful verification clears a stale soft offer (`RECOVERABLE_OFFERS`).
- **VERIFY_ID phrasing.** `services/verification_reply.py` sends code-owned
  facts (remaining count, accepted/received field labels, remembered request,
  emotion, refusal count, delegate note, expiry, whether to mention the human
  button; the current message redacted) to one best-effort structured call.
  The wording is accepted only if it contains no claim/policy numbers, digit
  runs, e-mail addresses or completion claims and names at least one accepted
  field; otherwise, or on any model failure, the previous template is used and
  the service status is not marked unavailable. The verification decision,
  failure budget, cooldown and delegate checks are unchanged.
- **Email summary.** `build_email_summary` assembles claim status/outcome, the
  validated answers, and follow-up items (documents still needed, appeal
  deadline) from permission-checked reads; a delegate without those grants
  gets an explicit note instead. The pending offer exposes `preview` and a
  masked recipient; the UI shows both, and the send receipt names the masked
  recipient. Consent still comes only from the buttons.
- **Answers.** `read_denial_reason` evidence now includes `documents_needed`
  and `appeal_deadline`; the composer receives the caller's emotion for one
  acknowledging sentence and may offer related help. The mechanical fact check
  canonicalizes spelled-out dates ("March 18, 2026") before comparing them with
  the evidence, so a correctly recorded date in another format passes while a
  changed date is still blocked. Stray tool names in `sources` are ignored as
  long as real evidence is cited. Optional reads outside an action's allowlist
  are simply not requested; the allowlist is still enforced in the harness.
- **README** gained an English quick start, demo script and a table of what
  code versus the model decides per phase.

## Verification

- Backend regression suite: **300 passed** (up from 282; new coverage for the
  escalation floor, stale-offer clearing, superseded clarifications, same-turn
  processing, composer facts/validation/fallback, summary structure, date
  canonicalization, planner allowlist filtering). Frontend: **8 passed**.
- Live default suite against the configured model (disposable in-memory
  sessions, 29 chat turns, no real e-mail): **8/8 scenarios passed**, including
  the two new ones. `emotional_pushback` produced, unverified: "I understand
  that this situation is frustrating for you. Verification is necessary to
  protect your personal information before we can discuss any claim details. I
  see that you've provided your full name, but I still need 2 more items from
  the following: date of birth, phone number, email address, or the last four
  digits of your ID. Your request to understand the denial reason is noted and
  will be handled immediately after verification." No transfer was offered on
  the first complaint, and the remembered denial question was answered right
  after verification. `off_topic_after_verification`: "What is RL?" was
  classified `out_of_scope` after a completed case, the retry offered a human,
  and the following appeal-deadline question completed with an empty backlog.
- The first live run on this build exposed one regression (the model spelled
  the appeal deadline as "March 18, 2026" and the numeric check blocked the
  answer) and one planner mismatch (`read_appeal_deadline` plan requested
  document guidance); both were fixed as described above and re-run.

Model wording still varies between runs; the assertions check substance
(unverified, an accepted field offered, an explanation present, no claim data,
no premature transfer), not exact text.

## Reproduce

```bash
MEMORY_BACKEND=memory python3 -m pytest -q
node --test tests/frontend_verification.test.cjs
python3 -m scripts.smoke_live            # spends API credits, at most 40 turns
```

## Deployment

`insurance-claims-agent-demo` now runs `insurance-claims-agent:sop-polish` on
`127.0.0.1:8000` with the existing `insurance-claims-data:/app/data` volume.
The previous container is stopped and kept as
`insurance-claims-agent-demo-before-sop-polish` for rollback; do not run both
against the same volume.

## Addendum: delegate workflow check over the HTTP API (same day)

A delegate (authorized representative) run against the live container exposed
three more problems, all fixed and re-verified with 10/10 checks through
`/api/chat`, `/api/email-choice` and `/api/email-status` only (probe sessions
deleted afterwards; no real e-mail):

- A question whose answer was blocked (`output_blocked`) or failed stayed at
  the head of the backlog and every later question was queued behind it.
  Failed items are now marked: transient failures (`llm_unavailable`,
  `tool_failed`) are retried *after* the caller's next question; blocked or
  denied items are superseded by it. Disallowed requests (`claim_update`,
  `document_upload`) are marked at the handoff gate for the same reason, and an
  explicit claim number that matched nothing is not carried into the next
  question.
- "For claim CL-2011, how much did the insurer actually pay, and what does
  net_fee mean?" was classified `general_claim_question`, so the amounts were
  never read and the composer's answer was (correctly) blocked. The intent
  fields now carry schema descriptions that define each intent; the same
  message is classified `payment_question` and answered from the recorded
  amounts. When the model extracts the same wording twice with a specific and
  the fallback intent, only the specific one is kept.
- "Please update her mailing address on the claim" is now `claim_update`
  (routed to a human) instead of a general question. Offers created by a
  blocked answer, an unsupported request or a missing delegate grant are
  retired by a later successful answer.

Scenarios covered: verification with the delegate's own three fields (in one
message and step by step, including a phone number), the policyholder's
details refused as delegate identity, CL-2048 denial and appeal deadline,
CL-2011 amounts and `net_fee` meaning, another customer's claim refused,
a claim change routed to a human, and the mock summary sent to the
policyholder's masked e-mail. Grant restrictions (revoked, expired, action or
claim not listed) remain covered by `tests/test_fixture_workflow.py`.
Regression suite: 304 passed.

## Addendum: fixture review (same day)

Reviewing `fixtures/` against today's date and the intent list surfaced three
gaps, fixed and checked over the HTTP API:

- **Expired deadlines.** Both denied claims carry appeal deadlines earlier than
  today, but PROCESS_CASE had no notion of "today". `get_claim_for_action` now
  derives `appeal_deadline_passed` and `as_of` (injectable clock
  `tools.claims.current_date`) for the actions that expose the deadline; the
  answer prompt states plainly that the deadline has passed and points to a
  human for late options, and the email summary marks it "(already passed)".
  Live: "The appeal deadline ... was 2026-03-18, and that deadline has already
  passed as of 2026-09-30. ... a human claims representative can review whether
  any late options exist."
- **How to appeal.** The guideline fixture gained an `appeal_process` follow-up
  rule; `read_appeal_deadline` may now read follow-up guidance, and "how do I
  appeal?" is answered from that text (including that the chat cannot file it).
- **New claims.** A `new_claim` intent routes "I want to file a new claim" to a
  human like other unsupported requests; disallowed-request replies now use
  readable labels ("filing a new claim", "changes to a claim or policy").
- **National-ID customers.** The identity wording asks for "the last four
  digits of the ID on your policy (SSN or national ID)" and, after a mismatch,
  suggests checking the ID type without revealing which type is on file. In
  practice the extractor rarely tags the ID type, so matching digits verify
  regardless of the word the caller used.

Regression suite: 309 passed; frontend 11 passed; live probes 7/8 (the one
"failure" was the probe expecting a mismatch that the extractor does not
produce).

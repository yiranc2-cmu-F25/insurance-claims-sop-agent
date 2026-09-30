# Design notes

## Turn pipeline

Every chat message runs through the LangGraph in `claim_agent/workflow/graph.py`:

1. `pause_gate` – nothing runs while a simulated human transfer is active.
2. `session_guard` – verification expiry (15 min idle / 1 h total) and the
   per-session failure cooldown are enforced server-side.
3. `input_guard` – full SSNs and API keys/tokens are blocked before any model
   call and never stored.
4. `security_gate` – one structured model call classifies threat intent
   (low / medium / high). Medium asks an ownership or requested-access
   question; high refuses and offers a human; an unusable assessment is a
   503, never a default "low".
5. `capture_turn` – two structured calls: identity extraction (values must be
   literal spans of the current message) and business understanding
   (dialogue act, intent, scope, emotion, claim hints, up to five questions).
   Whatever is useful is stored regardless of phase: hints said during
   verification are reused afterwards.
6. `handoff_gate`, `format_gate`, `scope_gate` – explicit human requests,
   malformed identity fields and off-topic messages are answered here.
7. The phase node: `verify`, `resolve` + `authorize`, `process`, or
   `post_process`.

## What code decides and what the model decides

| Phase | Code | Model |
| --- | --- | --- |
| VERIFY_ID | Which fields count, when three match, failure budget, cooldown, what may be said (no claim data), when to offer a human | Wording of the reply: acknowledging emotion, explaining why verification is required, accepting a refusal and offering alternative fields. The wording is validated (no claim/policy numbers, digits, e-mail addresses or completion claims; an accepted field must be offered) and falls back to a fixed template. |
| RESOLVE_INTENT | Claim ownership, delegate authorization per action and claim, the intent → action allowlist, disambiguation when several claims match | Which bounded path fits the caller's wording |
| PROCESS_CASE | Mandatory reads, ≤3 tool calls, field-level scoping per action, claim-ID/number/date/deadline checks, an independent review call | Which optional reads help; composing the answer from evidence only |
| POST_PROCESS | The email offer, consent by button only, summary content from permission-checked reads | Recognizing follow-up questions; wording for non-claim remarks |

Decisions and grounded answers run at temperature 0. Replies that carry no
facts of their own (verification wording, conversational turns) use a
temperature-0.7 instance so they do not read like a template; a stock opener
is stripped in code if it still appears.

## Identity and sessions

- A caller verifies with any three of full name, date of birth, phone, email
  and ID last four; a volunteered policy number is a consistency check only.
  Registered aliases (name, phone, email) are accepted.
- A delegate verifies with their own details, names the customer, and every
  later action is checked against the authorization on file (status, dates,
  claim list, action list). A family relationship alone grants nothing; the
  customer's own details cannot authenticate a delegate; the email recipient
  is always the customer's registered address.
- A conversation is bound to its first verified caller. A different role, a
  different customer, or a confirmed correction that verifies as someone else
  is refused, current access is dropped, and a new conversation is requested.
- Before verification, corrections to an already collected field are proposed,
  confirmed, and then re-verified. After verification, a conflicting field is
  treated as another person (refused, with a "Start a new conversation" button).
  The customer records in the fixtures are never modified.

## Requests, memory and backlog

Each message may contain several questions. They are queued and answered in
order (at most three per turn), each with its own ownership, permission and
evidence checks. A question the agent had to ask about, or one it could not
answer, never blocks the next real question: it is superseded by it (or
retried after it for transient failures). Answers to several questions form
one reply: later parts start with the substance, clarifications come last.
Per-case historical notes and a bounded, redacted recent context help resolve
references; claim facts are always re-read from tools.

## Grounded answers

The harness reads the selected claim (fields limited to the authorized
action), optionally document guidance, field definitions or a follow-up rule
from the fixtures, then asks the model for an answer citing its evidence. The
answer is rejected if it cites no real evidence, mentions another claim,
contains a number or date not in the evidence (spelled-out dates are
canonicalized first), or talks about a deadline when the evidence records
none; a separate review call then checks factual support and relevance.
Deadlines are reported relative to today, so an expired appeal deadline is
stated as passed with a pointer to a human representative.

## Emotional support and scope

Emotion is classified every turn. A single complaint gets an empathetic reply
that explains the gate and still moves the SOP forward. A human transfer is
offered after two consecutive distressed turns when the model recommends it,
after three regardless, after two refusals to verify, on request, or whenever
the workflow cannot proceed; progress retires a stale offer. Off-topic
questions are declined at any phase and the second one offers a human.

## Email summary

After a completed answer the UI offers an email summary with a preview:
claim status/outcome, what was discussed, and follow-up items (documents
still needed, appeal deadline and whether it has passed) from
permission-checked reads. Only the buttons confirm the choice; typing "yes"
does nothing. Delivery is mocked with a pending → approved sequence.

## Persistence and privacy

State lives in encrypted SQLite (single worker) with bounded retention; the
HTTP layer and the checkpointer scrub prohibited inputs; audit events record
tool use without PII. The demo has no login: a random HTTP-only cookie is the
session, and "New conversation" deletes it.

## Verification

- `tests/` – 329 backend tests with simulated model responses (routing,
  permissions, validation, failure handling) and 11 frontend tests; no token
  needed.
- `scripts/smoke_live.py` – opt-in scenarios against the configured model in
  disposable sessions (sample caller, emotional pushback, off-topic after
  verification, delegate flow, corrections, email buttons).
- `docs/history/` – working notes from development, kept as evidence.

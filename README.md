# Insurance Claims SOP Agent

A claims-support chat agent that follows a fixed four-phase SOP
(`VERIFY_ID -> RESOLVE_INTENT -> PROCESS_CASE -> POST_PROCESS`) while
conversing naturally. Code owns the workflow and every gate; the LLM owns
understanding and wording, and each model output is validated before it can
act. It needs an OpenAI-compatible model with JSON-schema structured output;
without one the UI shows `LLM: Unavailable` and the chat API returns 503.

**Hosted demo:** https://insurance-claims-sop-agent-qmcj.onrender.com (free Render
instance: it sleeps when idle, so the first request can take up to a minute).

## Quick start

```bash
cp .env.example .env        # set MODEL_API_KEY (default model: gpt-4o-mini; MODEL_BASE_URL optional)
docker build -t insurance-claims-agent .
docker run --rm -p 8000:8000 --env-file .env -v insurance-claims-data:/app/data insurance-claims-agent
```

**Deploy to Render (free tier):** the repo includes a `render.yaml` blueprint.
On Render choose *New → Blueprint*, pick this repository and enter
`MODEL_API_KEY` when prompted. The free instance keeps sessions in memory and
sleeps after 15 idle minutes, so the first request can take up to a minute.

Open http://127.0.0.1:8000 and click **Use the sample caller** (Margaret Chen,
policy POL-9921, DOB 1985-03-15, SSN last four 4472, denied healthcare claim
from January). To run without Docker see [Run locally](#run-locally); design
details are in [docs/design-notes.md](docs/design-notes.md).

## Assignment coverage

| Requirement | Where |
| --- | --- |
| Four phases in a fixed order, transitions decided by code | `claim_agent/workflow/graph.py` |
| VERIFY_ID: no claim detail before three PII fields match; partial answers, clarification questions, refusals and alternative fields handled conversationally | `workflow/verify_id.py`, `services/verification_reply.py` |
| RESOLVE_INTENT / PROCESS_CASE: messy language resolved to a bounded path; answers only from tool data, at most three reads, mechanical checks plus an independent review | `workflow/resolve_intent.py`, `services/case_harness.py` |
| POST_PROCESS: email summary (status/outcome, what was discussed, next steps) sent or skipped by button only | `services/email_followup.py` |
| Out-of-scope questions declined politely; a human offered after repeats | `workflow/gates.py` |
| Memory across phases: hints given during verification are used afterwards; several questions per message are queued | `workflow/intake.py`, `services/request_queue.py` |
| Bonus: emotion recognition, empathetic explanation of the gate, persuasion, alternatives, escalation only after sustained distress | `services/handoff.py`, `services/verification_reply.py` |
| Delivery: this repo + Dockerfile, API token via `MODEL_API_KEY`, chat UI showing the phases | `Dockerfile`, `web/` |

Tests: `MEMORY_BACKEND=memory python3 -m pytest -q` (329 tests, no token
needed), `node --test tests/frontend_verification.test.cjs`, and an opt-in live
check `python3 -m scripts.smoke_live` against the configured model.

## Project structure

```text
insurance_claims/
├── api.py                       # Startup entry point; keep `uvicorn api:app`
├── claim_agent/
│   ├── agent.py                 # Backward-compatible LangGraph entry point
│   ├── paths.py                 # Locations of fixtures, frontend and other resources
│   ├── api/                     # HTTP layer
│   │   ├── app.py               # Creates the FastAPI app and mounts the frontend
│   │   ├── routes.py            # Chat, email-button and human-handoff endpoints
│   │   ├── schemas.py           # Validation of client request bodies
│   │   ├── sessions.py          # Cookie sessions and per-session locking
│   │   └── presenters.py        # Returns only the state the frontend may see
│   ├── workflow/                # SOP workflow layer
│   │   ├── graph.py             # Node wiring and phase routing; start reading here
│   │   ├── state.py             # Session state and cross-phase memory fields
│   │   ├── intake.py            # Per-turn extraction and memory updates
│   │   ├── gates.py             # Safety, format and scope gate nodes
│   │   ├── verify_id.py         # VERIFY_ID
│   │   ├── resolve_intent.py    # RESOLVE_INTENT and action authorization
│   │   ├── process_case.py      # PROCESS_CASE
│   │   ├── post_process.py      # POST_PROCESS
│   │   └── common.py            # Small helpers shared by nodes
│   ├── llm/                     # Model integration
│   │   ├── client.py            # API configuration, calls and failure handling
│   │   ├── prompts.py           # Understanding, planning, answering and review prompts
│   │   └── schemas.py           # Structured output formats for the model
│   ├── guardrails/              # Input hygiene, PII normalization, safety and permission rules
│   ├── services/                # Controlled business operations
│   │   ├── case_harness.py      # Tool allowlist, call budget, answer review
│   │   ├── email_followup.py    # Email choice, current-offer binding, duplicate-send protection
│   │   ├── verification_session.py # Verification lifetime, failure cooldown, frontend countdown
│   │   ├── verification_reply.py # LLM-phrased VERIFY_ID replies with template fallback
│   │   ├── identity_corrections.py # Identity corrections, re-verified after confirmation
│   │   ├── request_queue.py     # Multi-question backlog and per-turn limits
│   │   ├── checkpoints.py       # Blocks full SSNs and keys/tokens before persistence
│   │   ├── sqlite_memory.py     # Encrypted database, retention, session deletion
│   │   ├── memory_policy.py     # Chat length limits, redacted recent context
│   │   ├── case_memory.py       # Per-case historical notes
│   │   └── handoff.py           # Human transfer: offer, pause, resume and handoff summary
│   └── tools/                   # Data access and tools
│       ├── fixtures.py          # Loads the demo data
│       ├── identity.py          # Customer lookup and identity matching
│       ├── claims.py            # Claim ownership checks and field-level reads
│       ├── documents.py         # Document requirements and follow-up guidance
│       └── email.py             # Mock email delivery
├── web/
│   ├── index.html               # Page structure
│   └── static/
│       ├── styles.css           # Page styles
│       └── app.js               # Chat, email and human-transfer button interactions
├── fixtures/                    # Sample data, including the demo delegate identity and grant
├── tests/                       # Behavior and security regression tests
├── langgraph.json               # LangGraph entry configuration
├── Dockerfile
└── requirements.txt
```

Suggested reading order: [workflow entry](claim_agent/workflow/graph.py) →
[state definition](claim_agent/workflow/state.py) → the phase files →
[controlled execution](claim_agent/services/case_harness.py) →
[tool implementations](claim_agent/tools/).

`workflow` decides where to go next; `services` constrain and execute business
operations; `tools` actually read data or simulate sending. Prompts live in
`llm/prompts.py`, permissions in `guardrails/policy.py`, buttons in
`web/static/app.js`. `api/schemas.py` describes frontend request formats and
`llm/schemas.py` describes model output formats; do not mix them. Import tools
from `claim_agent.tools`; the implementation is split by business capability,
so callers do not need to know the data-file layout.

The directory refactor preserved all HTTP paths, cookie names, the four-phase
order and button behavior. `api.py` and `claim_agent/agent.py` only forward to
the new locations and do not create a second app or session graph. Custom
scripts that imported internal modules (for example `claim_agent.nodes`) must
switch to the locations above. Existing containers must be rebuilt and
restarted to load the new layout and static files; mount the Docker data
volume described below to keep sessions and the encryption key.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn api:app --reload
```

Open http://127.0.0.1:8000.

## Enable an LLM

To enable conversations, configure a model token:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
MODEL_API_KEY=your-api-token
MODEL_NAME=your-model-name
MODEL_BASE_URL=
```

For an OpenAI-compatible provider, set `MODEL_BASE_URL` to that provider's API base URL. Rebuild and restart the container after changing `.env`:

```bash
docker build -t insurance-claims-agent .
docker rm -f insurance-claims-agent-demo
docker run -d --name insurance-claims-agent-demo -p 8000:8000 --env-file .env -v insurance-claims-data:/app/data insurance-claims-agent
```

## Run with Docker

```bash
docker build -t insurance-claims-agent .
docker run --rm -p 8000:8000 --env-file .env -v insurance-claims-data:/app/data insurance-claims-agent
```

## Demo input

```text
I'm the policyholder. My name is Margaret Chen, policy POL-9921.
I'm calling about my denied healthcare claim from January.
DOB is 1985-03-15, SSN last four is 4472.
```

The demo verifies three PII fields, remembers the claim hint, finds claim `CL-2048`, explains the grounded denial reason, and asks whether to send an email summary.

### Conversational security clarification

The safety model classifies threat intent, not whether identity has already
been verified. Ordinary self-identification and partial PII answers proceed to
`VERIFY_ID`. A caller's declared role is stored separately from verified identity;
it never grants claim access or substitutes for a delegate authorization check.
The classifier receives only bounded role/phase metadata and the last approved
clarification question, not saved PII, claim records, or raw conversation history.

Ambiguous protected-data requests produce an ownership or requested-access
question selected by the model. After two unresolved assessments the UI offers
human support instead of repeating the question indefinitely. Claim lookup and
email sending remain disabled until a new explicit low-risk assessment and all
normal verification/authorization gates pass. Model failures remain unavailable;
there is no keyword override or automatic low-risk fallback.

Manual check in the chat UI: send `I'm Margaret Chen, the policyholder. My policy
is POL-9921.` and expect a request for two more identity fields. Send a DOB next
and expect one more field. A clarification such as `It's my own policy` must not
authenticate the caller. For ambiguous access, two unresolved turns should offer
the **Transfer to human** button. These checks use the configured model; wording
may vary.

Identity and business understanding use separate structured model calls. The
identity call receives only the current message, a declared role, and names of
pending correction fields: no saved PII, transcripts, or case notes. Identity
values must be literal source spans in the current message before formatting;
unsourced values and redaction placeholders cannot update identity or trigger
customer-facing PII format errors. Invalid values actually volunteered by the
caller still receive format feedback. The history-aware business schema cannot
return identity fields, change roles, or confirm identity corrections. Existing
three-field matching, correction confirmation, expiration, and delegate grants
remain enforced. This separation adds one model call per conversational turn. In VERIFY_ID one more
best-effort call phrases the reply from code-supplied facts; if it fails or goes
off-script, a fixed template is used, so the gate never depends on it.

New claim hints are source-bound too: `CL-...` and `POL-...` have distinct types;
a year must occur in the current message outside its identity spans. Semantic
type/status/month hints require a literal supporting quote. A new question must
have a current-message source; stored requests use redacted user text rather
than an invented rewrite. Single-question turns retain their current context
(for example, that a lab cannot reissue a report). The LLM labels email-only
and identity-only replies separately, so they cannot recreate old questions.
Switching cases clears active filters and summaries while preserving separate
historical notes. Legacy unproven years and policy-as-claim IDs are discarded
on the next successfully understood turn; conversation history is not deleted.

For medium-risk ambiguity only, one bounded semantic review checks whether the
entire message merely supplies identity data. A source-bound confirmation lets
normal format/identity validation continue. High-risk decisions are never
overridden this way, and review/API failure remains unavailable, not low risk.

Follow up with `What is the appeal deadline?` or `What documents should I send?`.
In `POST_PROCESS`, click **Yes, send summary** or **No, skip**. Only these
buttons confirm the email choice; typing "yes", "no" or "send it" in chat
does not send or skip an email. A new claim question continues the conversation
and invalidates the previous offer; a successful answer creates a new offer.

The buttons call `POST /api/email-choice` with `{ "offer_id": "...", "choice":
"send" }` (or `"skip"`). The server checks the browser session, business phase,
verified identity and current offer. Recipient, claim and summary come from
server state, never the request body. An offer is bound to the validated summary
shown at that point; stale or cross-session offers are rejected. Repeated clicks
on the same choice return the saved result without another tool call.

Clicking does not invoke the LLM. An existing validated offer can still be
confirmed if a later intent/answer call fails **after a successful safety check**.
If the safety check itself fails, requests clarification, or blocks the request,
new sends and approval polling are disabled until a later message passes that
check; the previous low-risk result is not reused. Skip remains available.
Identity and authorization checks still apply. Buttons are hidden while the bot is paused for a simulated human transfer
and restored after returning. Skip never calls the email tool. The tool remains
a mock: no actual email or original document attachment is sent.

State and recorded email decisions are now saved in encrypted SQLite by default,
with per-session locking for one server process. A file lock rejects a second
worker using the same data directory. Saved decisions survive restart, but a
crash between a real external send and saving its receipt still needs provider-side
idempotency and a transactional outbox; the mock is not an exactly-once mail service.

## Fixture-backed workflow

The four phases are unchanged and the LLM still handles language understanding;
code owns data ownership, authorization and the allowed operations.

- **Delegates**: `caller_role=delegate` is different from asking for a human
  representative. The caller's own identity must first match at least three PII
  fields; then the authorization on file is looked up by the customer's name or
  policy number and checked for validity dates, accessible claims and permitted
  actions. Every lookup and email action re-checks it. A family relationship is
  not an authorization.
- **Case selection**: an explicit `case_id` is supported and matched only among
  the claims accessible to the caller or the authorized customer. Several
  matches ask the user to choose; zero matches are stated explicitly. Switching
  cases clears the current hints and email summary while keeping bounded
  per-case historical notes, so old facts are never mixed in.
- **Amounts**: `payment_question → read_claim_amounts` reads the amounts
  together with their meanings from `claim_schema.json`; example values never
  enter the evidence. `net_fee` is not treated as the patient's amount due, and
  expected payments are not promises.
- **Documents**: the guidance read returns specific alternatives, general advice
  and the conditions for manual human review. When the caller says neither the
  original nor any substitute is obtainable, a Transfer to human button is
  offered. If no document-specific guidance exists, the gap is stated instead of
  invented, and generic auto-claim materials are never presented as required
  for a case.
- **Email**: still confirmed only by the Yes/No buttons. The recipient is always
  the customer's registered email; a delegate cannot redirect it. The grant must
  include `send_email_summary`, otherwise sending is refused.

David's identity and authorization in `representatives.json` are **hand-written
demo data**, not an authorization proven by source data and not production
validation rules. The demo grant runs from 2026-01-01 to 2030-12-31 and allows
read-only queries plus summary sending for the listed P9 claims; it does not
allow claim changes or document uploads.

Test the delegate in a new session (a private browser window gives an
independent session):

```text
I'm David Chen, calling on behalf of my mother Margaret Chen, policy POL-9921.
My own DOB is 2004-06-20 and my SSN last four is 6028.
Why was her healthcare claim from January denied?
```

After verification, continue with:

```text
For claim CL-2011, how much did the insurer actually pay, and what does net_fee mean?
Now switch to CL-2048. I cannot get the pathology report or any readable replacement from the lab. What can I do?
```

Removing the grant, setting `status` to `revoked`, adding an expiry date or
removing actions/claims blocks the corresponding access; restore the demo
configuration after testing.

### Mock email approval

The customer's Yes/No **consent to send** and the **simulated delivery
approval** that this demo derives from `consent_scenarios.json` are two
independent states; neither represents delegate authorization or connects to a
real approval or email service.

Delivery starts as `pending`; the page calls `POST /api/email-status` about
every 1.5 seconds and the next read moves to `approved`. The server rate-limits
reads and stores progress: after at most 5 checks (including the first) or 30
seconds, the next check reports `timeout` and offers human help without calling
the send tool again.

Set `DEMO_EMAIL_SCENARIO=timeout` in `.env` and restart to test the
never-approved case; `default` restores the normal flow, and an unknown value
fails instead of auto-approving. Reloading the page restores the waiting state;
with the page closed there is no background polling, and the next check after
reopening applies the timeout rule. You can keep asking questions while
waiting, but a session has only one pending summary at a time; completing the
old approval never overwrites a newer question or email choice, and approval
does not advance while the bot is paused for a human transfer. Every result is
labelled **No real email was sent**, and no original files are attached.

## PROCESS_CASE harness details

The model proposes `CaseReadOptions`: whether document guidance is needed and
which optional follow-up topic applies. Code maps these capabilities to a
`CasePlan`; the harness inserts the mandatory claim read first, document guidance
for document requests, and field definitions for amount questions. The model
cannot omit these SOP prerequisites or mismatch a topic with its tool. The
complete plan is validated before executing any tools. Each action has an explicit tool allowlist;
the verified party, selected claim and action are injected by the server.
The model cannot supply identity, arbitrary tool arguments, shell commands or
an email recipient. Every claim adapter checks ownership and returns only the
action's permitted fields.

If originals and reasonable substitutes are exhausted, the authorized document
workflow uses a fixed claim-and-guidance read plan before offering manual human
review. This recovery does not depend on another discretionary planning decision.

Each queued question is bounded to at most one model plan, **3 unique read tool calls**, one
answer-generation call and one independent model review. A chat turn processes
at most **3 questions / 9 read calls**; remaining questions wait for the next turn.
Model calls have a
12-second timeout and no automatic retries. There is no autonomous execution
loop. The current tools read local fixtures; when replacing them with HTTP/DB
adapters, configure timeouts in those adapters as well.

Answers require valid evidence references; claim IDs and numeric facts are
checked against evidence, then the model reviews factual support and relevance.
Markdown numbered-list ordinals are formatting, not business numbers; numeric
facts inside those items are still checked.
Rejected output, invalid plans, tool errors and unavailable models stop the
turn without presenting the rejected answer or offering email. Semantic model
review reduces hallucinations but is not a proof of factual correctness.

Audit events record request ID, event/outcome, allowed action/tool and execution
time; they omit PII, prompts, answers and raw exception text. The latest 200
events per session are kept in graph state and also emitted through Python
logging (`claim_agent.services.case_harness`, INFO). This is demo auditing, not a persistent
audit store. Graph checkpoints contain bounded conversation and identity data;
their payloads, metadata and pending writes are encrypted in SQLite by default.

## Semantic security

`guardrails/security.py` does not use keywords or regular expressions to judge
whether a user is malicious, and there is no default-low fallback after a
failure. Every message that passes the input guard gets one structured LLM
safety assessment with the shared timeout and error handling; the only extra
context is the current phase, the declared caller role, whether verification is
complete and whether a clarification is pending. No customer data or claim
history is added.

- Ordinary questions, complaints, refusing to share personal data, legitimate
  delegates and portal password recovery must not be blocked because a word
  appears; unrelated topics are left to the scope check.
- Clarification is asked only when protected data is requested and the
  subject/representation is genuinely unclear; it never transfers to a human
  automatically, and missing identity data is not treated as malicious.
- Explicit requests for someone else's data, internal secrets or bypassing
  controls are refused with a human button; the user can still continue with
  legitimate questions.
- A missing model, timeout, API error, missing fields, or an inconsistent
  output/risk classification makes `/api/chat` return 503, the page shows
  unavailable, and later business steps stop; lookups and email cannot reuse an
  earlier safety clearance.
- Low risk only means the business workflow may proceed; it is neither
  verification nor permission. Three PII fields, delegate grants, claim
  ownership, tool allowlists and button-only email consent remain in force.

`input_guard.py` still blocks full SSNs and real keys/tokens and validates
identity-field formats; that is not keyword-based intent guessing. The safety
classifier can still misjudge; the automated tests below use simulated model
results to verify routing and permissions, not a real model's accuracy. After
connecting your model, review ordinary conversations, negations/quotations,
legitimate delegates and unauthorized requests by hand.

## Tests

```bash
pytest -q
node --test tests/frontend_verification.test.cjs
```

Tests use simulated structured model responses; no API token is needed for
`pytest`. They verify enforcement and failure handling, not a real model's
language accuracy. Run the conversation examples against your configured model
before submitting the demo.

An opt-in live check covers the screenshot's multi-turn document follow-up,
invalid PII, correction/reverification, delegate access, cross-customer denial,
email buttons, off-topic handling, and simulated human transfer:

```bash
docker build -t insurance-claims-agent .
docker run --rm --env-file .env insurance-claims-agent python -u -m scripts.smoke_live
```

This command consumes API credits (at most 40 chat turns), uses disposable
in-memory sessions, and never opens the normal conversation database. It does
not retry failed scenarios to hide model variability. It prints pass/fail
results without raw identity values or API credentials. Email and human
handoff remain simulated. Passing this smoke check is not a production safety
guarantee or a replacement for broader model evaluations.

The email action is mocked; no real email is sent. The mock summary uses the
validated case answers from the conversation.

The demo does not require login. Instead, the server creates a random HTTP-only browser cookie and uses it as the LangGraph thread ID. The chat API ignores client-supplied session IDs. This prevents casual session-ID guessing; production should additionally bind the session to an authenticated user or tenant, use HTTPS, and add durable session revocation and cross-session rate limits.

## Identity lifetime and memory

- A conversation is bound to the first caller who verifies in it. A different
  role, a different customer, or an identity correction that verifies as another
  person is refused, current access is dropped, and the caller is asked to start
  a new conversation; the original caller can verify again to continue.
- By default, after **15 minutes** idle or **1 hour** since verification, at
  least three matching identity details must be provided again. Ordinary chat
  refreshes the idle timer but cannot extend the one-hour cap. Page reloads,
  status polling, email buttons and human-transfer buttons do not extend
  verification.
- A yellow countdown appears at the top of the page with **2 minutes** left;
  after expiry a "please verify again" notice stays and sending email is
  disabled. The backend checks independently at chat, page-read, button and
  tool boundaries and does not rely on the browser timer.
- Expiry clears the current identity, claim details, case notes, the recent
  chat used for the model and old email offers, but keeps unfinished questions
  and case-selection hints. Chat already displayed in the browser is not erased;
  old archives follow the retention policy below. Pending questions continue
  after re-verification.
- After **5** accumulated full-identity mismatches, verification is paused for
  **15 minutes** in the current session and a human button is offered. Partial
  answers, ordinary chat, format errors and model failures do not count as
  mismatches; a successful verification or the end of the cooldown resets the
  counter, and switching roles or correcting fields does not. This limit is per
  session and does not replace production cross-session brute-force protection.
- Before verification, changing an already collected identity field stores the
  candidate value and asks whether to adopt it; the LLM interprets
  confirm/cancel and code re-verifies. After verification, a conflicting
  identity field is treated as another person: access is dropped and a new
  conversation is suggested. Customer records in the fixtures are never
  modified.
- Several questions in one message are split by the LLM into separate backlog
  items: at most 5 per message, 10 pending, 3 processed per turn. Each is
  checked for permission and evidence separately; only successfully answered
  items are removed and failed ones stay. Say `continue`, explicitly replace the
  questions, or cancel the backlog. Email buttons appear only after the backlog
  is done; answers about different cases are not mixed into one email, and the
  offer is bound to the validated summary of the last case.
- The HTTP entry point blocks full SSNs and recognizable keys/tokens before
  LangGraph; the checkpointer also scrubs newly written checkpoints and pending
  writes for sync and async graph calls. This is not complete DLP for arbitrary
  secrets and does not clean earlier process history or external tracing/logs;
  manage those separately before deployment. Business PII lives in runtime
  memory and is stored encrypted with the checkpoints.

Lifetimes and cooldowns are configured through the `VERIFICATION_*` settings in
`.env.example`; restart after changing them. For a quick countdown demo, start
locally with `VERIFICATION_IDLE_SECONDS=130`, verify, and stay silent: the
notice appears after about 10 seconds. Do not use that value normally.

All tests simulate model results and use a controllable clock; the frontend
countdown test only needs Node.js, sends no network requests and spends no API
tokens.

## Bounded, persistent memory

The default is `MEMORY_BACKEND=sqlite` with the database at
`data/conversations.sqlite3`; no separate database service is needed. The first
start generates `data/memory.key`, readable and writable only by the current
system user, and later starts reuse it. Data payloads, metadata and in-flight
workflow writes are Fernet-encrypted; random session IDs and node names in the
index are not ciphertext. A missing or mismatching key raises an error instead
of silently clearing the database or falling back to plaintext.

Keeping the key in the data directory is a convenience for the local demo; it
**does not defend against someone who obtains both the database and the key**.
For real deployments inject a separately managed Fernet key through
`MEMORY_ENCRYPTION_KEY` and set up encrypted backups, access control and key
rotation. Never commit, print or send the key; `data/` is excluded from Git and
the Docker build context. The database connection supports one application
process only; multiple replicas and the Windows file-lock implementation are
not supported yet.

- **Chat and archives**: each current state keeps the last 20 messages and each
  session the last 30 node checkpoints (not 30 turns); chat and SQLite archives
  are retained for at most 24 hours by default. Reading the page does not renew
  retention; a session with no new writes for 24 hours expires, including
  unverified ones.
- **Automatic cleanup**: expiry is checked at startup and on every database
  read/write, and a cleanup also runs once a minute while the service is up.
  No cleanup runs at shutdown; the next start cleans before serving. Unbounded
  in-memory data from older versions is not migrated automatically.
- **Recent context**: at most the last 6 redacted messages, each at most 800
  characters, help resolve references such as "the second one" or "that one".
  Extracted personal fields and common email/phone patterns are masked, but this
  is not complete anonymization of arbitrary text. The current user message is
  still passed to the LLM for identity extraction; verification and email
  consent are never restored from old messages.
- **Case notes**: at most 5 cases with the last 5 tool-validated Q&A entries
  each, tagged with time, source, caller and customer; the model receives at
  most 2 per case and current permissions are re-checked. Notes are historical
  background only: answers still query tools, the email contains only the
  current case's valid discussion, and old offers are never restored from notes.
- **Clear button**: `Clear saved conversation` on the page, after confirmation,
  calls `DELETE /api/conversation`, which deletes only the current browser
  session's archives, in-flight writes and case notes, rotates the session ID
  and clears the page chat; old email buttons become invalid, and external
  actions already executed cannot be undone. Deletion is irreversible and does
  not cover separately stored backups, logs or content already shown in other
  open tabs.
- **Test mode**: `MEMORY_BACKEND=memory` keeps the original in-memory backend;
  the automated tests force an isolated in-memory or temporary SQLite store,
  never open your real session database and never call a real model.

Deployments must keep the same data volume and key to recover after a container
rebuild; the verification clock is stored too, so a restart does not extend
identity validity. Reloading the page restores the current session state and
the last reply; the full chat history is not redrawn automatically.

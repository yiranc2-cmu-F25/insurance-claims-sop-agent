# SOP and memory repair — 2026-09-30

## Changes

- Separate claim IDs from policy/identity fields. Literal claim references and
  fragments of hyphenated identifiers cannot overwrite personal identity.
- Require current-message evidence for new requests and claim search hints.
  An unspecified year, a DOB year, or a policy number cannot become a claim hint.
  Store redacted customer wording rather than model-invented question rewrites.
  Keep the full current context for a single short question, including exhausted
  recovery attempts. A stale top-level intent without a sourced question cannot
  manufacture a new request.
- Label email-only and identity-only replies semantically; neither creates new
  claim questions. Mixed messages can still contain genuine claim questions.
  Existing send/skip buttons and simulated delivery are unchanged.
- Preserve early requests through verification, separate notes per case, clear
  active filters on case switches, and remove unproven legacy years and incorrectly
  typed case IDs on the next successfully understood turn. No database/history
  deletion or broad conversation reset is performed.
- Let the model select optional read capabilities. The harness constructs the
  mandatory first claim read, required document/amount lookups, and matching
  follow-up tool. Permissions, source checks, maximum three reads per question,
  and independent answer review remain enforced.
- Add one bounded semantic identity-only review for medium-risk ambiguity.
  Missing/invalid PII can proceed to format validation, never directly to access.
  High risk is not overridden; provider failure remains unavailable.
- Ignore Markdown ordered-list ordinals in numeric-fact checking, but continue
  checking amounts, dates, claim IDs, and numbers inside list items.
- Once originals and substitutes are exhausted, use the authorized fixed
  claim/document-guidance path and offer human manual review. Clarify pronoun
  references through current-case context instead of requiring the document name
  again. The handoff remains simulated.

The design follows the OpenAI Docs distinction between schema adherence and
semantic correctness: structured output can still contain incorrect values, so
source binding, workflow invariants and targeted evals remain necessary.
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

## Verification

Real-model tests used disposable in-memory sessions against the Docker application
and actual configured provider calls; no model mocks, saved user conversations,
real email delivery, or actual human transfer were used.

- The six-scenario default live run passed, 21 chat turns: screenshot reproduction
  with early intent and incremental identity, malformed PII, correction and access
  checks, delegate lookup, scope/handoff, and email-button behavior.
- Focused case-switch memory and exhausted-document recovery passed, seven chat
  turns. Switching CL-2048 → CL-2011 → CL-2048 preserved separate case notes and
  identity, did not carry old search filters, and did not invent a year. Declining
  email in chat did not create a new query or change button consent.
- A separate two-turn document-follow-up check passed after fixing ordered-list
  numeric false positives and moving mandatory reads into the harness.
- Frontend verification tests: 7 passed. No frontend code changed.
- Final backend regression suite: 282 passed (34 more than the previous 248).
- After deployment, three additional real HTTP chat turns passed on the running
  service: policyholder verification/denial lookup, document alternatives, and
  a plain thank-you. The thank-you executed zero case tools and preserved the
  existing email offer with no consent change. `/api/health` returned `ok` and
  `/api/llm-status` returned `available`. Only the isolated probe session was
  deleted afterward; existing user conversations were untouched.

These are latest per-scenario results during iterative development, not one
statistical reliability run. Intermediate versions exposed list-number false
positives, inconsistent planning flags, and missed document-exhaustion context;
code/schema changes were made before rerunning affected scenarios. No retry loop
was added to production or tests. Semantic model decisions can still vary.

The non-failing LangChain `allowed_objects` deprecation
warning remains unrelated to the repaired workflow behavior.

## Reproduce

```bash
docker build -t insurance-claims-agent:sop-memory-fix .
docker run --rm --network none -e MEMORY_BACKEND=memory -e MODEL_API_KEY= \
  insurance-claims-agent:sop-memory-fix python -m pytest -q
docker run --rm --env-file .env -e MEMORY_BACKEND=memory \
  insurance-claims-agent:sop-memory-fix python -u -m scripts.smoke_live
docker run --rm --env-file .env -e MEMORY_BACKEND=memory \
  insurance-claims-agent:sop-memory-fix python -u -m scripts.smoke_live \
  --scenario alternatives_and_handoff --scenario case_memory_switch --trace-plan
```

Live checks spend API credits. The deployed service should use the existing
encrypted `insurance-claims-data` volume, with only one running worker/container.

## Deployment

The running `insurance-claims-agent-demo` container uses
`insurance-claims-agent:sop-memory-fix` at `http://127.0.0.1:8000`, retaining the
existing `insurance-claims-data:/app/data` volume and localhost-only port binding.
The stopped previous container is preserved as
`insurance-claims-agent-demo-before-sop-memory-fix` for rollback. Do not start
both containers against the same volume simultaneously.

# Automated test report — 2026-09-30

Later implementation and verification: [SOP and memory repair](sop-memory-fix-2026-09-30.md).
The findings below describe the pre-repair builds and are retained as evidence.

## Follow-up: targeted real-API testing

This later run tested the deployed `insurance-claims-agent:identity-source`
image, with only the current live-test script mounted read-only. It did not
change application code, prompts, the running demo, or stored conversations.
FastAPI routes ran through TestClient in disposable in-memory sessions; the
configured model provider was called for real, with no model mocks or retries.
Browser rendering and the running HTTP server were not tested in this run.

Following [OpenAI Docs evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices),
checks focused on actual workflow outcomes, permissions, and tool decisions,
not HTTP success alone. All 15 chat turns returned HTTP 200, but only 2 of 8
scenario variants completed all assertions. One further isolated real-model
identity-extraction call confirmed a new field-binding failure. These variants
target known failures and are not a representative production success rate.
No backend/frontend regression suite was rerun. No real email was sent.

| Scenario | Turns | Outcome / evidence |
| --- | ---: | --- |
| Invalid DOB, then three-digit last-four | 2 | Failed: DOB format feedback worked; last-four input hit `clarification_required` before format validation. |
| Delegate using policy number and January hint | 1 | Failed: verified caller, but `case_id=POL-9921` prevented claim selection. |
| Email flow using the original policyholder introduction | 1 | Blocked at setup: the same policy-as-claim error also affected the policyholder. Email behavior was not reached here. |
| Document follow-up after explicit claim selection | 2 | Failed: plan omitted mandatory first `get_claim_for_action`; harness rejected it with `tool_not_allowed`, zero case tools. Identity remained unchanged, with no PII format error. |
| Specific pathology-report alternative | 2 | Failed: `For CL-2048, I cannot get the original pathology report. Can I provide an alternative?` produced `pii_errors=[policy_number]`, zero case tools. The later alternatives-exhausted handoff step was not reached. |
| Email flow after explicit claim selection | 3 | Mixed, scenario failed: `Yes, send it.` became `document_submission`, ran two read tools and replaced the offer. Consent stayed unset. Button send/repeat and skip/repeat passed; send after skip returned 409. Delivery was simulated. |
| Delegate with explicit CL-2048, then cross-customer CL-3001 | 2 | Passed: correct delegate verified and authorized claim answered; other customer's claim was not selected, zero case tools, and no other-customer denial reason appeared. |
| Malformed last-four with explicit policyholder role, then correction | 2 | Passed: appropriate last-four format feedback, no premature verification/tools, then successful verification and case completion with corrected data. |

### New findings and diagnosis

1. **Claim/policy field confusion is bidirectional.** Besides `POL-9921` being
   placed in `case_id`, the identity extractor put literal `CL-2048` in
   `policy_number`. The isolated extraction call reproduced this exactly. The
   current source check accepted it because the string really occurs in the
   message; the subsequent format check then blamed the user. Literal source
   validation alone does not validate which field the user supplied.
2. **Invented business memory.** Five successful explicit-case setup turns
   contained `year=2023`, although the test user never supplied a claim year.
   The exact claim ID takes priority in `select_claim`, so it did not block
   these selections. It remains an unvalidated search-memory value that could
   affect a later lookup without an exact ID; that later effect was not tested.
3. **The role context changes malformed-input handling.** The same three-digit
   value received format feedback when the user explicitly said they were the
   policyholder, but received a security clarification in the partial-input
   scenario. Missing/malformed PII is still being confused with ambiguous access.

The older planner and consent-only intent failures remain reproducible. No
observed test required relaxing identity, ownership, or email-consent gates.
The explicit-ID variants are diagnostic contrasts, not fixes for the original
natural-language inputs. Measured chat-route latency in the 11-turn second run
was 3.48–8.30 seconds; model-request token usage/cost was not instrumented.

### Reproduce the targeted variants

From the project root, against the same image and current test script:

```bash
docker run --rm --env-file .env -e MEMORY_BACKEND=memory \
  --mount "type=bind,source=$(pwd)/scripts/smoke_live.py,target=/app/scripts/smoke_live.py,readonly" \
  insurance-claims-agent:identity-source python -u -m scripts.smoke_live \
  --scenario invalid_identity --scenario delegate_and_email --scenario email_buttons --trace-plan

docker run --rm --env-file .env -e MEMORY_BACKEND=memory \
  --mount "type=bind,source=$(pwd)/scripts/smoke_live.py,target=/app/scripts/smoke_live.py,readonly" \
  insurance-claims-agent:identity-source python -u -m scripts.smoke_live \
  --scenario document_followup --scenario alternatives_and_handoff \
  --scenario email_explicit_case --scenario delegate_explicit_case \
  --scenario identity_recovery --trace-plan
```

The script now records per-turn latency and separate email-button sub-results.
The original default scenario set and 30-chat-turn cap are retained; the focused
variants are opt-in. Run each once for comparison, not repeatedly until green.

## Earlier run

## Scope and result

The identity-source fix is implemented. The history-aware business schema no
longer contains identity fields. A separate current-message-only extraction
produces literal identity spans; code checks their source before formatting.
Model-invented values and redaction markers cannot overwrite identity or create
customer-facing PII format errors. Genuine malformed current input is still
validated. Existing verification, correction, expiry and delegate gates remain.

- Backend regression suite: **248 passed**, including 15 new provenance cases.
- Frontend tests: **7 passed**.
- First live-model run: **2/5 scenarios passed**, 16 chat turns.
- Diagnostic replay: both selected failure scenarios reproduced, 7 chat turns.
- Independent email-button check: consent, skip, same-choice idempotency and
  conflicting-choice rejection passed, 3 chat turns; inspection also found a
  consent-only intent error. The script now explicitly asserts that no new
  query or replacement offer is created by consent-only chat; that stronger
  assertion has not been rerun in this report.

The live calls used the configured model and disposable in-memory sessions.
No saved user conversation was opened by the checks. No real email or human
transfer occurred. No failure was converted into a pass through automatic retry.

## Findings still requiring work

| Finding | Observed evidence | Effect / boundary |
| --- | --- | --- |
| Document planner omits the mandatory first read | The plan returned `get_document_guidance_for_claim` and `get_claim_followup_guidance`, omitting `get_claim_for_action`. | The harness correctly returned `tool_not_allowed` with zero tool calls. Identity remained verified and `pii_errors` was empty, but the requested answer was not delivered. |
| Malformed identity input is over-classified as ambiguous access | A current three-digit last-four answer produced `security_status=clarification_required` before the format validator ran. | No verification or claim read occurred, but the user got an ownership clarification instead of the appropriate format correction. |
| Delegate request confuses policy and claim identifiers | The caller and authorization were verified, but `intent_hint.case_id` was `POL-9921`, a policy identifier. | No case was selected and no protected case tool ran. The lookup needs claim-ID type/source validation, not broader permissions. |
| Consent-only text is interpreted as a new business request | `Yes, send it.` became `document_submission`, executed two read tools and created a new summary offer. | Email consent still remained unset until a button click; however, unnecessary business processing occurred. |

## What passed in live conversations

- Repeated self-identification and incremental identity collection completed.
- Early healthcare/denial/January hints survived verification in the policyholder flow.
- The screenshot's document follow-up no longer produced an ID-last-four error
  or changed the collected identity; the separate planning failure above remains.
- A real identity correction suspended access; rejecting it restored the original
  identity through verification.
- Cross-customer case access and explicit unauthorized bulk-access requests did
  not execute case tools or disclose the other customer's denial details.
- Repeated off-topic requests offered a human transfer; clicking it paused chat,
  and returning to the bot worked.
- Email sends remained button-only and simulated; skips did not send.

## Reproduction

```bash
docker build -t insurance-claims-agent .
docker run --rm --network none -e MEMORY_BACKEND=memory -e MODEL_API_KEY= insurance-claims-agent python -m pytest -q
node --test tests/frontend_verification.test.cjs
docker run --rm --env-file .env insurance-claims-agent python -u -m scripts.smoke_live
docker run --rm --env-file .env insurance-claims-agent python -u -m scripts.smoke_live --scenario screenshot_followup --scenario delegate_and_email --trace-plan
```

The opt-in live runner spends API credits and caps each run at 30 chat turns.
Model behavior can vary between runs. Mocked regression tests verify enforcement,
not model language accuracy, so their green result is not an end-to-end pass.
One non-failing LangChain deprecation warning concerns the future default for
`allowed_objects` in serialization; it is not the cause of these failures.

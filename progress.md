# Implementation progress

## Current submission status

The create/list/reschedule/cancel chatbot is implemented with confirmation,
follow-up drafts, local-time rendering and non-blocking overlap support. Latest
offline verification: **232 tests passed**. The historical live acceptance run
recorded 21 passes and one blocked Google-only mutation case, followed by focused
context and overlap checks. See README.md and the sanitized report in reports/.

Entries below are a chronological development journal. Earlier pending-status
statements and intermediate test counts describe their original point in time;
later entries supersede them. Raw JSON evidence and recovery journals mentioned
below are local-only artifacts excluded from the submission.

## Milestone 1 — configuration and read-only client

Implemented on 2026-11-12. Milestone 2 is recorded separately below.

### Delivered behavior

- Explicit private `.env` loading with process-environment precedence, required
  fields, configurable Ollama model/endpoint and IANA timezone validation.
- Cal.com event type discovery/details, slot ranges, and booking listing through
  a read-only client with endpoint-specific version headers and Bearer auth.
- UTC request conversion and typed results with aware UTC timestamps.
- Booking cursor pagination with loop detection and a configurable page ceiling;
  malformed or incomplete responses fail rather than returning partial success.
- Sanitized authentication, permission, rate-limit, HTTP, network, timeout, and
  invalid-response errors. No raw provider exception context is retained.
- Fixed API origin, no redirect following, no environment proxy inheritance,
  and no credential loading at import time. No mutation methods or model calls.

### Automated tests and checks

Executed from the repository root:

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 29 passed in 0.17s (initial run). |
| `.venv/bin/python -m pytest -q` | 29 passed in 0.05s (final run). |
| `.venv/bin/python -m compileall -q calcom_agent tests` | Passed; exit code 0. |
| `git diff --check` | Passed; no whitespace errors. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --offline --python .venv/bin/python -r pyproject.toml --extra test` | Passed; resolved/checked 13 packages. Confirms documented dependency setup. |
| `git check-ignore .env .venv` | Passed; both paths are ignored. |

Tests use synthetic fixtures and `httpx.MockTransport`; no project `.env` is read.
Coverage focuses on configuration precedence/validation, endpoint versions/auth,
slot UTC conversion, empty results, pagination/filter forwarding, malformed
responses, invalid input before HTTP, redirect rejection, sanitized provider
errors and transport exception context. There were no pre-existing tests or
implementation files, so no separate existing affected suite was available.

Environment setup observations:

- System `python3 -m pip --version` failed: pip is not installed.
- Initial `uv venv .venv` failed because uv's default cache is read-only.
- `UV_CACHE_DIR=/tmp/calcom-uv-cache uv venv .venv` succeeded.
- Sandboxed dependency downloads failed DNS resolution. The same installation
  completed with approved network access:
  `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --python .venv/bin/python httpx python-dotenv pytest`
- Installed direct verification dependencies: HTTPX 0.28.1, python-dotenv 1.2.4,
  pytest 9.2.0. A filesystem hardlink warning fell back to copying successfully.

### Live API checks

**Not run.** Credentials were not read and no authenticated account requests were
made. Official public API documentation was consulted; that is documentation
research, not a live account check. Account permissions, event-type policies,
availability and calendar connection settings remain unverified.

### Manual UI verification

**Not run.** There is no UI in milestone 1.

### Review paths and remaining limitations

- `calcom_agent/config.py`: explicit configuration loading and validation.
- `calcom_agent/client.py`: request boundary, normalization, UTC conversion,
  endpoint versions, and bounded booking pagination.
- `tests/`: mocked contracts and meaningful failure cases.
- `README.md`: setup, optional counts-only live verification, and checklist.

Assumes a single account and personal event types. Event type records preserve
booking fields/locations but are not a full scheduling-policy schema. Booking
records intentionally omit attendee/contact details for now. No retries, rate-limit
scheduling, model integration, conversation state, local-day overlap logic, UI,
booking mutations, or Google Calendar event management are included. Session
and scheduling behavior belong to later milestones. API correctness is verified
against mocks and current documentation, not the user's account.

## Milestone 2 — deterministic scheduling rules and confirmation

Implemented on 2026-11-12. Milestone 3 is recorded separately below.

### Delivered behavior

- `SchedulingService` prepares create, cancel, and reschedule proposals using
  real read-client interfaces, without a model or UI. Missing details return a
  stable code and field names. Inputs include attendee identity/timezone, custom
  booking answers, supported duration, and a real available slot.
- Booking references resolve against a fetched candidate list by UID, displayed
  one-based number, or exact case-insensitive title. Missing/ambiguous matches
  fail explicitly rather than selecting a booking arbitrarily.
- Naive local wall times resolve through IANA timezones. DST gaps fail; repeated
  times require an explicit fold. Datetimes sent for scheduling must be aware
  and future-dated. Slot arithmetic is performed in UTC across DST boundaries.
- Immutable proposal summaries and opaque tokens bind confirmation to a private
  payload snapshot. A new preparation attempt revokes the previous proposal,
  even if the edited request is invalid. Default confirmation lifetime: 5 minutes.
- Python requires explicit `confirmed=True` plus the exact current token. It
  consumes the token before refresh/execution, checks expiry, re-reads event and
  booking state, compares snapshots, and rechecks availability before execution.
  Expired, stale, unconfirmed, or repeated actions never reach the executor.
- Rescheduling preserves duration and passes `bookingUidToReschedule` for slot
  checks. Cancel/reschedule refuse inactive or past bookings and disabled policies.
- The existing event-type reader now preserves the small set of policy fields
  required by these rules, including duration options and unsupported event flags.
- An `ActionExecutor` protocol defines the future write boundary. Only fake
  executors are used in this milestone; no Cal.com HTTP write adapter exists.
  Executor failures propagate without automatic retry or replaying confirmation.

### Automated tests and checks

All commands executed from repository root, using synthetic data only:

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_scheduling.py` | 15 passed in 0.05s. |
| `.venv/bin/python -m pytest -q tests/test_client.py tests/test_config.py` | Existing milestone 1 suite: 29 passed in 0.05s. |
| `.venv/bin/python -m pytest -q` | Full suite: 44 passed in 0.06s. |
| `.venv/bin/python -m compileall -q calcom_agent tests` | Passed; exit code 0. |
| `git diff --check` | Passed; no whitespace errors. |

Focused cases cover confirmation and payload binding, invalid edits, custom-field
requirements/types, duration choices, ambiguity, timezone day boundaries, DST
gaps/folds, disabled and unsupported policies, expired/stale proposals, unavailable
slots, refresh failures, executor failures, and duplicate execution prevention.
An integration test uses the actual read client with `httpx.MockTransport` to
verify duration-policy normalization and the prepare/refresh/execute boundary.
No automated test failures remained. No new dependencies were required.

### Live API checks

**Not run.** No private `.env` or credentials were read, and no authenticated
requests or live writes were made. Actual account policies remain unverified.

### Manual UI verification

**Not run.** There is still no UI; it belongs to milestone 4.

### Review paths and manual verification checklist

- `calcom_agent/scheduling.py`: local-time resolution, candidate resolution,
  prepare methods, private action snapshots, and `execute_confirmed` gate.
- `calcom_agent/client.py`: event-type policy preservation; HTTP remains read-only.
- `tests/test_scheduling.py`: fake-executor and mocked HTTP verification.

Optional local manual checks (synthetic data/fake executor only):

1. Prepare a create action and inspect its explicit date/time/attendee summary.
2. Attempt execution without confirmation; verify the fake executor is untouched.
3. Confirm once, then repeat the token; verify exactly one executor invocation.
4. Change the booking, policy, or available slots between preparation and
   confirmation; verify refusal and prepare a fresh proposal.
5. Try a duplicate title and a repeated DST wall time; verify clarification is
   required through the returned error code. These manual checks are **Not run**;
   their underlying behavior is covered by automated tests above.

### Assumptions and remaining limitations

One service instance belongs to one trusted user's session; pending state is
in-memory and expires on restart. Natural-language date parsing, clarification
wording, conversation state, and chat confirmations are later integration work.
The caller must supply a candidate list and explicit structured details.

The take-home scope remains free standard personal event types. Paid, recurring,
seated, instant, and email-verification types are rejected. Multiple locations
require a choice and attendee-defined locations are currently unsupported.
Custom answer validation is intentionally basic (required values, JSON safety,
and common primitive field types); complete custom field schemas, SMS workflows,
and location collection remain unsupported and must be verified before live use.
Staleness compares the normalized event/booking fields currently retained by the
client, not all provider metadata or attendee changes. Cal.com remains responsible
for final conflict/policy enforcement when a future write adapter is implemented.
No live mutation adapter, write reconciliation, persistent idempotency, concurrent
multi-user execution, model integration, UI, or Google event management is added.
The gate prevents reuse within this in-memory session; it does not claim provider
exactly-once delivery across process restarts or unknown write outcomes.

## Milestone 3 — Ollama conversation and validated tools

Implemented on 2026-11-12. Milestone 4 is recorded separately below.

### Delivered behavior

- Provider-neutral `ModelProvider`, `ModelReply`, and `ToolCall` types; separate
  Ollama HTTP adapter with configurable model/endpoint, non-streamed tool calling,
  thinking disabled, a 60-second HTTP timeout, and sanitized failure codes.
  No new dependencies or agent framework were introduced.
- A session-local `Conversation.turn()` loop retains user messages and tool
  results for missing-information follow-ups, numbered booking references, and
  corrections. An injected clock and explicit local today/tomorrow dates ground
  relative-date interpretation; deterministic timezone/DST validation remains in
  Python. The model is instructed to clarify ambiguity and use actual slots.
- An allowlist exposes event-type discovery, booking listing, availability, and
  three proposal-preparation tools. Python checks required fields, primitive
  types, unexpected fields, discovered event IDs, and saved booking references.
  Multiple tool calls per response are rejected; each turn allows at most six
  model requests. Malformed calls return errors for clarification/recovery.
- Listing saves chronologically numbered candidates and filters period overlap
  locally, including bookings crossing midnight. Slots are bounded to 20 entries
  per result with an explicit truncation flag.
- A proposal immediately stops model processing and displays the Python summary.
  Only a subsequent exact `yes`, `confirm`, or `yes, confirm` invokes the existing
  confirmation gate directly. The model cannot execute actions or supply its own
  confirmation. Corrections revoke prior confirmation; explicit rejection and
  session reset discard pending state.
- Fake executors exercise all four intents. No executor is configured by default:
  confirmation then explicitly reports that no booking was changed. The HTTP
  Cal.com client remains read-only; there is no live mutation adapter.
- `scripts/check_ollama.py` provides an opt-in local Qwen3 scenario check using
  synthetic reads and a fake executor. It never loads `.env` or contacts Cal.com.

### Automated tests and checks

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_conversation.py` | Final run: 21 passed in 0.31s. |
| `.venv/bin/python -m pytest -q tests/test_client.py tests/test_config.py tests/test_scheduling.py` | Final run, existing suites: 44 passed in 0.22s. |
| `.venv/bin/python -m pytest -q` | Final run: 65 passed in 0.18s. |
| `.venv/bin/python -m compileall -q calcom_agent tests scripts` | Passed; exit code 0. |
| `git diff --check` | Passed; no whitespace errors. |

Scripted model tests cover list/create/cancel/reschedule, missing attendee details,
corrections preserving prior details, numbered references, real-slot dispatch,
local-day overlap, DST clarification, unknown tools, invalid arguments, multiple
calls, bounded loops, reset, API/model errors, and duplicate confirmation. Adapter
tests use mocked HTTP for request/response contracts, malformed/incomplete
responses, no auth header, omission of thinking output, and transport failures.
One initial test-helper naming collision failed; it was corrected. All final
automated suites pass. No project `.env` was read.

### Local Ollama verification (separate from automated tests)

Local model discovery: `curl --silent --show-error --max-time 3 http://127.0.0.1:11434/api/tags`.
The sandbox initially blocked local sockets; the same read succeeded with
approved local network access. An installed `qwen3:8b-q4_K_M` was selected for
verification, without changing the configurable default or downloading a model.

The first `.venv/bin/python -m scripts.check_ollama --model qwen3:8b-q4_K_M`
failed because the model did not fetch listing candidates. A diagnostic rerun
successfully listed, proposed a reschedule, and proposed cancellation, but exposed
an incorrect relative date; the diagnostic was interrupted before completion.
The system context now explicitly supplies today/tomorrow and forbids anchoring
relative dates to bookings. The smoke script now asserts the exact reschedule UTC
timestamp as well as the operation, so the earlier erroneous date cannot pass.

The next `.venv/bin/python -u -m scripts.check_ollama --model qwen3:8b-q4_K_M`
passed listing but failed the exact relative-date assertion. A Python guard was
then added for simple unqualified today/tomorrow phrases, with a regression test
verifying rejection and repair before a proposal can be confirmed. A further run
passed listing but failed to produce a reschedule proposal after the guard
rejected the wrong date. This is a safe refusal, not a successful scenario check.
The adapter now bounds generation to 1,024 tokens with an 8,192-token context.

`.venv/bin/python -u -m scripts.check_ollama --model qwen3-coder:30b` then
**passed** (exit code 0) using the other already-installed Qwen3 model:
listing; rescheduling follow-up with exact UTC date assertion and confirmation;
cancellation; creation; and time correction preserving attendee details before
confirmation. All mutations used a fake executor. This is a live local-model
check, not an automated mocked test or live Cal.com check. The smaller model's
failures remain documented above; do not assume model tags are interchangeable
in accuracy. No model downloads or default configuration changes were made.

A final deterministic refinement makes simple today/tomorrow listings default
to exactly that local day when the model omits period bounds. This is covered by
the final automated suite; the live smoke used explicit period bounds.

### Live Cal.com API checks

**Not run.** No authenticated account requests, live booking writes, credentials,
or personal calendar data were used. Local Ollama testing uses synthetic fixtures
only and is not a Cal.com integration test.

### Manual UI verification

**Not run.** UI work remains milestone 4.

### Review paths and manual checklist

- `calcom_agent/model.py`: provider-neutral model contract.
- `calcom_agent/ollama.py`: provider wire format and error sanitization.
- `calcom_agent/conversation.py`: tool schemas, state, dispatch, clock context,
  loop bounds, and confirmation bypassing the model.
- `tests/test_conversation.py`: scripted conversations and mocked adapter tests.
- `scripts/check_ollama.py`: synthetic live-model verification.

Optional manual checks (not a UI):

1. Run the synthetic Ollama script with an installed model and inspect scenario
   outcomes. It requires no API key.
2. In a controller session with fake tools, omit attendee details and supply them
   in a follow-up; correct the time before confirming.
3. Verify a proposal alone does not invoke the executor, confirmation does so
   once, and an edited proposal needs a new confirmation.
4. Reset the controller and verify candidate numbers/pending actions are cleared.

### Remaining limitations

Natural-language interpretation depends on model accuracy; confirmation shows
the actual absolute time but does not prove that the model understood intent.
Scripted tests validate orchestration, not general language understanding.
The supported confirmation vocabulary is deliberately small. Other messages
revoke the old proposal and let the model clarify or prepare a corrected one.
History is session-only and currently unbounded; long chats should be reset.
Turns must be processed sequentially per user session. Model free-text answers
remain generated text; only tools supply verified calendar facts.

No UI, live write client, write-outcome reconciliation, persistent state, or new
advanced event-type support is included. No live model check can establish
account-specific API compatibility. Milestone 2 policy limits still apply.
Simple unqualified today/tomorrow start dates are checked against the clock in
the tool's timezone. Negated/complex relative phrases are left to model
interpretation and clarification; this is not a complete natural-language parser.

## Milestone 4 — Streamlit chat UI

Implemented on 2026-11-12. Milestone 5 is recorded separately below.

### Delivered behavior

- `app.py` provides a Python web chat with chat history, input, progress spinner,
  timezone/scope labels, readable proposal/error text, and conversation reset.
  It clearly states that booking changes are not enabled at this milestone.
- Each Streamlit browser session owns a `ChatSession`, controller, model provider,
  and Cal.com client. No conversation or credential-bearing resource is globally
  cached. The factory passes only model/endpoint settings to the model adapter.
  Connection cleanup is registered when the session is released.
- Submission callbacks queue events; each event is claimed and consumed before
  external processing. Ordinary reruns cannot replay it. Chat/reset controls are
  disabled during queued or active processing, and a session lock rejects
  concurrent processing/reset. Consumed IDs remain remembered through reset.
- Existing confirmation rules remain enforced. Repeated confirmation cannot
  re-execute a consumed proposal. New identical text is a new user submission,
  not automatically deduplicated by text content.
- Reset clears visible messages and controller history/candidates/proposals.
  Expected model failures remain readable; unexpected errors are not printed or
  logged, reset conversation context, and cannot replay a consumed request.
  Interrupted processing also clears context/pending proposals and retains the
  consumed event without replay.
- `.streamlit/config.toml` binds loopback port 8501, disables usage telemetry,
  and hides raw exception details. README documents VS Code SSH forwarding;
  only port 8501 needs forwarding, not Ollama.
- Streamlit 1.67.0 installed and declared as `streamlit>=1.50,<2`. No other new
  framework, model/scheduling behavior, or live mutation adapter was added.

### Automated tests and checks

All test data is synthetic. The UI factory is patched in AppTest tests, so tests
do not load the private project `.env` or make account/model network calls.

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_ui.py` | Final run: 6 passed in 0.56s. |
| `.venv/bin/python -m pytest -q tests/test_client.py tests/test_config.py tests/test_scheduling.py tests/test_conversation.py` | Existing suites: 65 passed in 0.08s. |
| `.venv/bin/python -m pytest -q` | Final full suite: 71 passed in 0.63s. |
| `.venv/bin/python -m compileall -q app.py calcom_agent tests scripts` | Passed; exit code 0. |
| `git diff --check` | Passed; no whitespace errors. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --offline --python .venv/bin/python -r pyproject.toml --extra test` | Passed; resolved/checked 41 packages. |

AppTest exercises two independent simulated browser sessions, message rendering,
reruns without repeated requests, reset, confirmation without duplicate fake
writes, configuration failures, and Ollama outage messages. A controlled slow
model verifies busy controls, rejected concurrent submissions/reset, and exactly
one processed event. Unexpected errors and interruption tests verify sanitized
output, consumed IDs, and cleared context. A factory test checks model-provider
configuration and the absence of a live executor.

Initial UI tests failed because AppTest resolves relative app paths against the
test file, not repository root. Tests now use an absolute path derived from the
test file. No test failures remain.

Dependency setup command:
`UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --python .venv/bin/python 'streamlit>=1.50,<2'`.
It succeeded with approved network access; the hardlink warning fell back to
copying. The committed dependency manifest was verified offline afterward.

### Actual loopback server check

`.venv/bin/python -m scripts.check_ui_server` — **Passed**, exit code 0.
The script started the actual Streamlit server on `127.0.0.1:8501`, received HTTP
200 and `ok` from its health endpoint and HTTP 200/HTML from the root page, then
terminated its child server. This used approved loopback socket access. It did
not open a chat/WebSocket session, load `.env`, or contact Cal.com/Ollama.
This is an HTTP smoke check, not visual/browser or laptop-forwarding verification.

### Live Cal.com / Ollama checks

- Live Cal.com API checks: **Not run**. No credentials or personal calendar data
  were read and no authenticated requests or booking writes were made.
- Live Ollama check for this milestone: **Not run**. UI tests simulate an outage
  and model replies; milestone 3's local-model results remain separate above.

### Manual UI and laptop verification

**Not run.** AppTest simulates sessions; it does not establish browser appearance,
actual browser isolation, or laptop access through the user's VS Code tunnel.

Manual checklist:

1. Privately configure `.env`, selecting a locally installed model, then run
   `.venv/bin/python -m streamlit run app.py` from repository root.
2. Forward port 8501 in VS Code and open the forwarded URL on the laptop.
3. Open a separate browser/private session; verify histories stay independent.
4. Submit a request and observe progress/disabled input/reset while it runs;
   check a subsequent rerun does not repeat the request/reply.
5. Prepare a booking change, reset, and verify the old proposal/history is gone.
6. Confirm a new proposal and verify the app reports execution is not configured.
7. With Ollama unavailable, verify a readable error and retained chat transcript.

### Important review paths and remaining limitations

- `app.py`: chat rendering, disabled controls, submission callback, reset, spinner.
- `calcom_agent/ui_session.py`: isolated ownership, queue/lock/consumption,
  error/interruption handling, and configuration-to-controller wiring.
- `.streamlit/config.toml`: loopback binding, telemetry and exception display.
- `tests/test_ui.py`: simulated UI sessions and focused lifecycle/failure tests.
- `scripts/check_ui_server.py`: temporary real-server health/root verification.
- `README.md`: startup and forwarding instructions.

The app has no login; it is intended for a trusted owner through a private SSH
tunnel. Browser reload/disconnect or process restart can lose session state;
there is no persistent delivery/idempotency guarantee. Reset retains HTTP clients
and their initial configuration; restart/open a fresh session to change settings.
History and consumed IDs remain in memory until the session ends. A slow model
may take several minutes across the existing six-request tool loop. The UI is
text-based and non-streaming. No real create/cancel/reschedule calls are possible
with the provided client. Live booking lifecycle verification remains milestone 5.

## Milestone 5 — booking write integration and controlled lifecycle

Implemented on 2026-11-12. Implementation is ready for review; live account/demo
verification remains pending. Milestone 6 has not started.

### Delivered behavior

- The Cal.com client implements the `ActionExecutor` boundary for create,
  reschedule, and cancel, using authenticated POST requests and endpoint-specific
  version `2026-02-25`. An individual booking GET supports cleanup verification.
  Official endpoint documentation was reconsulted before implementation.
- The web-session factory now supplies the client as executor to the existing
  Python confirmation gate. The UI explains that confirmation applies changes
  and may send invitations/calendar updates. Proposal preparation still performs
  no write, and all existing single-use confirmation/staleness rules remain.
- Mutation operations and payload fields are allowlisted, path UIDs are encoded
  and checked, and returned data is reduced to booking identifiers/status/time
  fields. No raw provider response or attendee fields are returned by the adapter.
- The adapter never retries POST. Timeout, network, server error, or malformed
  success output becomes `write_outcome_unknown`; the user is told to inspect
  bookings before retrying. No exactly-once or automatic reconciliation claim.
- Successful writes clear saved numbered references because rescheduling may
  return a different UID. Subsequent chat references require a fresh listing.
- `scripts/check_live_lifecycle.py` prepares an opt-in, model-independent local
  demo: user-designated event/attendee; actual slots; per-write confirmations;
  create/list/reschedule/list/cancel; returned UID tracking; cleanup verification.
  It reads credentials only when explicitly run by the user, never prints their
  values, hides attendee input, and prints no personal calendar records.

### Automated tests and checks

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_lifecycle.py` | 5 passed in 0.03s. |
| `.venv/bin/python -m pytest -q tests/test_client.py tests/test_config.py tests/test_scheduling.py tests/test_conversation.py tests/test_ui.py` | Existing suites: 71 passed in 0.68s. |
| `.venv/bin/python -m pytest -q` | Final run: 76 passed in 0.72s. |
| `.venv/bin/python -m compileall -q app.py calcom_agent tests scripts` | Passed; exit code 0. |
| `git diff --check` | Passed; no whitespace errors. |

The stateful mocked-HTTP lifecycle verifies no writes before confirmation,
authentication/version headers, create payload, list results, changed UID on
reschedule, reason field names, cancellation against the new UID, cleanup GET,
and duplicate-confirmation refusal. Failure tests verify conflict errors,
unsupported payload/operation and unsafe identifier rejection, and exactly one
POST for timeout/server/malformed-success failures with sanitized error context.
Existing tests verify conversation/UI behavior and factory executor wiring.
No credential file, personal data, or account endpoint was used in these tests.

### Live API and demo checks

**Not run.** The original instruction prohibits reading credential values, and
no designated test attendee/event type has been provided for agent-run writes.
The implementation and private local runner are ready; account-dependent
completion criteria have not been claimed as achieved.

- Live create → list → reschedule → list → cancel: **Not run**.
- Actual returned UID/status and live cleanup verification: **Not run**.
- Cal.com/Scheduling Demo destination and calendar sync inspection: **Not run**.
- Real busy-slot rejection and unrelated Google event conflict check: **Not run**.
- Live Ollama check this milestone: **Not run**; previous model results remain
  separate from API contract validation.

### Manual UI verification

**Not run.** Automated AppTest verification is separate from a real browser or
laptop port-forwarding test.

### Review paths and manual checklist

- `calcom_agent/client.py`: shared request transport, `execute`, allowlisted
  payloads, endpoint versions, safe result normalization, `get_booking`.
- `calcom_agent/ui_session.py`: live executor wiring through the existing gate.
- `calcom_agent/conversation.py`: clear numbered references after a write.
- `app.py`: confirmation/invitation notice.
- `tests/test_lifecycle.py`: mocked successful lifecycle and uncertain failures.
- `scripts/check_live_lifecycle.py`: explicit, private live verification runner.
- `README.md`: live-check instructions and calendar/busy-slot checklist.

1. Privately verify Scheduling Demo is the event's destination/conflict calendar.
2. Choose a free standard demo event type and an attendee address you control.
3. Run `.venv/bin/python -m scripts.check_live_lifecycle`, inspecting each selected
   time before confirming. Inspect Cal.com/Google Calendar privately between steps.
4. Verify final cancellation in Cal.com and calendar cleanup. If interrupted,
   inspect and manually cancel any demo booking before retrying.
5. Repeat through the chat UI; test unavailable-slot handling and an unrelated
   Google busy event, then remove temporary test artifacts manually.

### Remaining limitations

Live compatibility, permissions, invitations, calendar syncing, policy behavior,
and cleanup are not verified. The script supports the simple event-type scope
already documented; required custom questions need the chat flow or additional
private test inputs. Unknown write outcomes require user inspection; no persistent
reconciliation/idempotency ledger exists. Generic HTTP errors do not distinguish
every provider validation reason. The adapter is an internal write boundary;
application callers must use `SchedulingService.execute_confirmed`, not invoke
`execute` directly. All prior advanced-event, model, session and UI limits apply.

### Follow-up — actionable live-runner setup errors

The user reported the runner stopping immediately after `ready`, before the
event-type prompt. That ordering indicates configuration loading failed, but the
original handler masked the field name with a generic setup/cleanup message.
The runner now displays the safe `ConfigError` message (setting names only),
explains repository-root `.env` loading and environment precedence, and confirms
that no API requests or booking changes occurred at that stage. The actual
missing/invalid setting has not been inferred or inspected.

Verification:

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_live_runner.py` | 4 passed in 0.03s; mocked config failures name the setting and never construct an API client. |
| `.venv/bin/python -m pytest -q` | 80 passed in 0.68s, including existing suites. |
| `.venv/bin/python -m compileall -q scripts/check_live_lifecycle.py tests/test_live_runner.py` | Passed. |
| `git diff --check` | Passed. |

Live API checks and manual UI verification: **Not run** for this fix. No private
`.env` or credential values were read. Rerun the same live command privately to
identify the setting; do not share its value.

### Follow-up — clarify duration scope and Google Calendar setup

README now distinguishes the challenge's open-ended meeting wording from
Cal.com's event-type-backed booking contract and our existing-template scope.
Configured duration choices are supported; arbitrary lengths requiring template
creation/editing are not. A 30-minute template is a demo default, not a challenge
requirement. Added setup steps for multiple durations, availability/timezone,
Google conflict calendars, destination overrides, and meeting location.

Official booking, multiple-duration, calendar-selection and location documentation
was consulted. Verification: `git diff --check` passed. Automated tests: **Not run**
for this documentation-only update. Live API and manual UI checks: **Not run**.
No credential or personal calendar data was read. Empty event-type discovery
remains unresolved; documentation clarification does not claim to fix it.

## Extension — confirmed event-type creation

Implemented on 2026-11-12 at the user's request. This extends the prior
existing-template limitation; milestone 6 remains unstarted.

### Delivered behavior

- Added `prepare_create_event_type` to the model tool allowlist. A requested
  duration without a matching standard template can produce a reusable personal
  type proposal. Existing templates/multiple-duration options are reused first.
- Python validates title, safe URL slug, and positive whole-minute duration;
  discovers types before preparation; and rechecks duration/slug conflicts at
  confirmation. Discovery failures prevent creation. An edit revokes the old
  proposal, and existing expiry/single-use token rules apply.
- Confirmed template writes use `POST /v2/event-types`, Bearer authentication and
  `cal-api-version: 2026-06-12`, verified against the official creation reference.
  Minimal payload: title, slug, duration and Cal Video integration location.
- New templates use account-default availability/calendar settings. The proposal
  explicitly tells the user to verify Scheduling Demo as the account destination.
  No Google credentials or calendar IDs enter model arguments or this new tool.
- Returned event-type data is normalized and retained by the conversation under
  the API-provided ID. It reports that no booking exists yet; the user can ask to
  continue using earlier details, find slots, and confirm the booking separately.
  Creating a type alone does not create a Google Calendar event.
- The client allowlists template payload fields and location. Unknown write
  outcomes are sanitized and never automatically retried. No existing type is
  modified or deleted; no Google Calendar configuration API was added.
- README now documents the new flow, API contract, setup inheritance, separate
  confirmations, supported scope, and template cleanup.

### Automated verification

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_event_type_creation.py` | 5 passed in 0.04s. |
| `.venv/bin/python -m pytest -q tests/test_client.py tests/test_config.py tests/test_scheduling.py tests/test_conversation.py tests/test_ui.py tests/test_lifecycle.py tests/test_live_runner.py` | Existing suites: 80 passed in 0.67s. |
| `.venv/bin/python -m pytest -q` | Final run: 85 passed in 0.64s. |
| `.venv/bin/python -m compileall -q app.py calcom_agent tests scripts` | Passed. |
| `git diff --check` | Passed. |

The scripted conversation with stateful mocked HTTP verifies discovery → propose
template → confirm POST → cache returned ID → find slots → propose booking →
separate booking confirmation. No POST occurs before either confirmation. Tests
also cover reuse, slug conflicts, discovery changing before confirmation, invalid
inputs, discovery failures, duplicate confirmation, malformed success/server
errors, and no retries or sensitive provider exception context.

### Live and manual checks

- Live event-type/booking API checks: **Not run**. No private `.env`, credentials,
  or personal calendar data was read.
- Local Ollama check for the new tool: **Not run**; model-driven orchestration is
  tested with scripted replies, not claimed as live-model verified.
- Manual UI/Google Calendar verification: **Not run**.

Manual checklist: privately configure account defaults for Scheduling Demo;
request a duration not currently offered; inspect the proposed template; confirm
it; verify the new type privately in Cal.com; ask to continue; choose a real slot
and confirm the booking separately; verify the calendar entry; cancel the test
booking; manually remove the demo template if no longer needed.

### Remaining limitations

Creation only: existing-template editing/deletion and rescheduling to a different
duration remain out of scope. New templates use Cal Video and account defaults,
not an automatic Google Meet/calendar setup. Account-specific permissions,
inheritance and calendar syncing remain unverified. Empty discovery is still
unresolved and can undermine reuse checks; it is not silently declared fixed.
The tool refreshes discovery, but does not guarantee uniqueness across sessions
or unknown outcomes. Created templates persist after booking cancellation.

## Milestone 6 — reproducible setup and reviewer handoff

Completed on 2026-11-12 for implementation/documentation scope. Outstanding live
acceptance checks from milestone 5 remain explicit; no live success is inferred
from mocked tests or server startup.

### Delivered handoff

- Created `ENV_SETUP.md`: Ubuntu Python/dependency setup; existing Ollama daemon
  and exact model tags; safe private `.env` setup without overwriting an existing
  file; environment precedence; Cal.com/Google prerequisites; Streamlit startup;
  laptop VS Code forwarding and alternative SSH tunnel commands; verification,
  troubleshooting, and shutdown. Laptop requires only SSH/browser, not Python,
  Node.js, API keys, or a separately running frontend.
- Added `requirements.lock` pinning 41 runtime/test packages for the verified
  Ubuntu/Python 3.12 environment. Direct dependency ranges stay in pyproject.toml.
  Verified both declared dependencies and pinned setup in separate fresh venvs.
- Updated README startup to use pins and preserve any existing private `.env`.
  Added a guide link, architecture/review map, illustrative (not live) demo
  conversation, dependency refresh command, and clear verification boundaries.
- Documented current limits: personal standard types, confirmed template creation
  only, inherited account calendars/Cal Video, no unrelated Google-event CRUD,
  no automatic retry or exactly-once guarantee, session-only state, model accuracy
  differences, and unresolved empty discovery.
- Consulted current official uv, Ollama Linux, Streamlit and VS Code Remote SSH
  setup documentation for the commands. No production/app behavior changed.

### Exact automated setup/verification commands and results

Commands ran from repository root. Both temporary venvs were newly created;
offline installs used the existing package cache. This proves installation from
available cached distributions, not an uncached internet download.

| Exact command | Observed result |
| --- | --- |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv venv /tmp/calcom-milestone6-verify` | Created fresh CPython 3.12.16 venv. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --offline --python /tmp/calcom-milestone6-verify/bin/python -r pyproject.toml --extra test` | Installed 41 packages successfully. |
| `/tmp/calcom-milestone6-verify/bin/python -m pytest -q` | 85 passed in 1.02s. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip compile --offline pyproject.toml --extra test -o requirements.lock` | Resolved/pinned 41 packages. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv venv /tmp/calcom-milestone6-locked` | Created a separate fresh CPython 3.12.16 venv. |
| `UV_CACHE_DIR=/tmp/calcom-uv-cache uv pip install --offline --python /tmp/calcom-milestone6-locked/bin/python -r requirements.lock` | Installed all 41 pinned packages successfully. |
| `/tmp/calcom-milestone6-locked/bin/python -m pytest -q` | 85 passed in 1.00s, including all existing suites. |
| `/tmp/calcom-milestone6-locked/bin/python -m compileall -q app.py calcom_agent scripts tests` | Passed. |
| `git diff --check` | Passed. |

No new tests were added for documentation-only changes. Existing contract,
conversation, confirmation, lifecycle and UI tests were rerun to validate the
fresh pinned environment. No private configuration or real account data was used.

### Actual server check

`/tmp/calcom-milestone6-locked/bin/python -m scripts.check_ui_server` — **Passed**,
exit code 0. With approved loopback access, the fresh pinned environment started
Streamlit and returned health/root HTTP success on port 8501. Its temporary
server was then stopped. No chat session was opened and no `.env` was loaded.
This is not a visual browser, SSH tunnel, model, or live calendar test.

### Checks not performed

- Live authenticated Cal.com checks and real Google Calendar sync/cleanup:
  **Not run**. Earlier user-run attempts establish setup/discovery failures,
  not a successful lifecycle.
- Empty event-type discovery root-cause/account diagnosis: **Not run** against
  private configuration; remains unresolved.
- Local Ollama rerun for milestone 6/new template tools: **Not run**. Earlier
  model results remain documented separately; all final automated model tests
  use scripted replies/mocked HTTP.
- Manual browser rendering, actual two-browser-session use, and laptop VS Code
  or terminal SSH forwarding: **Not run**.
- Optional model download, service start, uv installer, and pip alternative:
  **Not run**; these are user setup instructions, not claimed performed actions.
- Uncached package download and other operating systems/Python versions:
  **Not run**. Pins target the verified Python 3.12 setup.

### Review and final manual checklist

Review `ENV_SETUP.md`, `requirements.lock`, README's Reviewer handoff section, and
the code paths mapped there. Follow the environment guide's verification sections
to complete the live acceptance work privately:

1. Confirm private configuration and correct personal-account discovery.
2. Start Ollama and Streamlit on Ubuntu; open port 8501 through the laptop tunnel.
3. Verify independent browser sessions and reset/progress behavior.
4. With a designated test attendee, verify create/list/reschedule/list/cancel and
   cleanup privately in Cal.com and Scheduling Demo.
5. Exercise missing-duration template creation with separate confirmations, then
   remove any unneeded test template manually.
6. Verify a busy Google event blocks slots without being listed as a booking;
   remove temporary test artifacts. Inspect unknown write outcomes before retries.

The handoff is ready for review. The project should not be described as fully
live-verified until the outstanding checks above have actually been completed.

## Follow-up — diagnose browser model HTTP failures

The user's browser reached Streamlit but received `model_http_error`. That
establishes app/tunnel connectivity, not a successful model/calendar workflow.
The adapter previously suppressed HTTP status as well as response content. It
now exposes only the numeric status in a safe code, e.g. `model_http_error_404`,
and the conversation supplies static setup hints for 404/400/500 responses.
Provider response bodies, prompts and credential/configuration values remain
hidden. The actual user's failure cause has not been verified; a missing model
tag is a hypothesis, not a finding.

Verification: `.venv/bin/python -m pytest -q` — 89 passed in 0.71s, including
mocked 400/404/429/500 checks and all existing suites.
`.venv/bin/python -m compileall -q calcom_agent tests` and `git diff --check` —
passed. Live model/API checks and manual UI verification for this fix:
**Not run**. No private `.env` was read.

### Follow-up — native Ollama URL normalization and missing-model detection

The user reported `model_http_error_404` after reaching the browser UI. Inspection
confirmed the old adapter doubled the API path when configured with a copied
`/api` or `/api/chat` URL. The adapter now normalizes these suffixes (and `/v1`)
before appending the native path. A 404 triggers one bounded, read-only `/api/tags`
diagnostic: an absent configured tag produces `model_not_installed`; an
inconclusive diagnostic retains the HTTP error. No automatic model substitution,
download, or generation retry is performed, and no response contents are echoed.

Live local checks performed with approved loopback access:

- `curl --silent --show-error --max-time 5 http://127.0.0.1:11434/api/tags`
  succeeded. Installed tags included `qwen3-coder:30b`; `qwen3:14b` was absent.
- A Python inline check constructed `OllamaProvider('qwen3-coder:30b',
  'http://127.0.0.1:11434')` and called `chat` with the synthetic prompt
  `Reply with OK.` and no tools. It returned a response successfully. No calendar
  data, user prompt, private configuration, or Cal.com request was used.
- A local HTTPX URL-construction check reproduced the doubled API path without
  making a network request. The actual user's `.env` was not read, so neither
  an incorrect configured URL nor missing tag is claimed as their proven cause.

Automated verification: `.venv/bin/python -m pytest -q` — 94 passed in 0.72s.
New mocked tests cover all supported endpoint forms and safe missing-tag
diagnosis. Live Cal.com checks and manual browser verification: **Not run**.

### Default model change

At the user's request, changed the Settings fallback, `.env.example`, and
synthetic Ollama check's default to `qwen3:30b-a3b`. README/ENV_SETUP now state the
new default. Private `.env` was not read or modified; existing file/environment
overrides still take precedence. Earlier successful Coder-model checks do not
establish accuracy for this new default.

Verification: `.venv/bin/python -m pytest -q tests/test_config.py` — 8 passed in
0.02s, including the new fallback assertion and existing override checks.
`git diff --check` passed. Live model/API and manual UI checks: **Not run**.

## Follow-up — diagnose transport failures in the actual UI session

The user reported `model_unavailable`. Added a Model connection panel displaying
only the selected model and server origin, with a metadata-only check using the
session's actual HTTP client. This distinguishes stale/different session settings
from a runtime connection failure without reading or displaying credentials.
Connect failures and dropped responses now have separate sanitized error codes;
raw exception text remains hidden.

Observed checks:

- Approved loopback `curl --silent --show-error --max-time 5 http://127.0.0.1:11434/api/tags`
  succeeded and confirmed `qwen3:30b-a3b` is installed.
- An approved inline Python call to `OllamaProvider('qwen3:30b-a3b',
  'http://127.0.0.1:11434').chat(...)` with synthetic `Reply with OK.` and no
  tools succeeded. No meeting details or private `.env` were used.
- The identical inline Python `check_connection()` with that explicit model/base
  returned `model_connection_failed` in the restricted agent runtime and `ready`
  with approved network access. This demonstrates a runtime socket restriction
  here, not a proven diagnosis of the user's independently started UI process.
- `.venv/bin/python -m pytest -q` — 98 passed in 1.85s, including mocked
  metadata diagnostics and AppTest coverage of the new panel without prompts or
  calendar calls.

Live Cal.com checks and manual browser verification: **Not run**. The user should
restart Streamlit from a normal Ubuntu SSH terminal and use the panel to identify
the failing session's actual endpoint/model and metadata status. No private `.env`
was read or modified.

## Follow-up — meaningful web UI error diagnostics

Implemented structured, allowlisted diagnostics in `calcom_agent/diagnostics.py`.
Model, Cal.com, execution and validation failures carry a safe code, stage,
readable summary, suggested next step and numeric HTTP status when available.
Terminal failures have a visible error summary; handled tool issues remain in a
Troubleshooting details expander even if the model recovers. Errors remain
attached to the specific reply across reruns and clear on conversation reset.
Connection checks and unexpected initialization failures use the same safe hints.

`Conversation` records model/tool/execution issues separately from model text;
`ChatSession` snapshots them alongside each reply and records sanitized unexpected
errors/interruption details. The UI renders structured fields without raw provider
responses, request headers, stack traces, credential values or calendar payloads.
Unknown model exception text is now sanitized in the chat reply as well as the
debug panel. Debugging does not bypass confirmation or retry writes.

Review paths: `diagnostics.py`, `conversation.py`, `ui_session.py`, `app.py`, and
`tests/test_ui.py`. README and ENV_SETUP describe the new panel and safe sharing.

Verification:

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_ui.py` | 10 passed in 0.79s. |
| `.venv/bin/python -m pytest -q` | 101 passed in 0.87s, including existing suites. |
| `.venv/bin/python -m compileall -q app.py calcom_agent tests` | Passed. |
| `git diff --check` | Passed. |

Focused AppTest cases verify model 404/status/next-step rendering and persistence,
Cal.com 403 tool details after a handled failure, and suppression of private text
in an unknown model exception. Existing tests continue to verify replay/reset,
confirmation and outage behavior. No live services or private `.env` were used.

Live API/model checks: **Not run** for this change. Manual browser verification:
**Not run**. After restarting Streamlit, privately exercise a connection failure,
expand Troubleshooting details, verify the stage/code/status and next action,
then recover/reset and verify details stay associated with the correct reply.

## Follow-up — measured timeout diagnostics and NVIDIA fault investigation

The user requested concrete diagnostics for model timeouts/HTTP 500s and reported
that GPU access disappeared after requests. Read-only investigation found:

- `ollama ps` initially reported the selected 30B model as **100% CPU**. A later
  snapshot reported **100% GPU**, so residency changed and metadata alone is not
  proof of GPU health.
- `nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader`
  failed with a device-handle Unknown Error / no devices found.
- `journalctl -k -b --no-pager -g 'NVRM|Xid' -n 12 -o cat` showed repeated
  NVIDIA GSP/RPC failures.
- `journalctl -k -b --no-pager -g 'Xid|fallen off|GSP.*timeout|GPU.*reset' -n 25 -o cat`
  identified **Xid 79 (GPU has fallen off the bus)** and **Xid 154 (Node Reboot
  Required)**. `/proc/driver/nvidia/version` identified the loaded open kernel
  module as 580.178.04; PCI enumeration still identified the RTX 3090.
- `free -m` showed about 27 GB available system RAM at that snapshot; this does
  not establish GPU-memory health or rule out a transient allocation failure.

These establish a host-level GPU fault, not its precise hardware/driver cause
or the exact triggering request. No driver changes, GPU reset, service restart,
or reboot was performed. Repeated generation tests stopped after identifying
Xid 79/154. GPU recovery and stability verification remain user/admin work.

### Application changes

- `ModelError` now carries structured diagnostic context. The adapter records
  actual elapsed request time, timeout phase, selected model/server origin,
  request method/path, timeout settings, message/tool counts, context/output
  limits and no-generation-retry policy. No prompt content or credentials.
- Failures trigger one bounded (2-second) `/api/ps` query for the selected model's
  loaded state, memory size and VRAM allocation. A zero-VRAM loaded model is
  marked CPU-only; GPU/mixed placement is not claimed as healthy-driver proof.
- HTTP error text is inspected only for fixed GPU/device, allocation, runner or
  context categories. The original text is never put into diagnostics or logs.
- A second allowlist in `diagnostics.py` filters context before UI rendering.
  Troubleshooting details now displays that context rather than repeating only
  a generic code. The Model connection panel can inspect loaded-model metadata.
- Added `OLLAMA_TIMEOUT_SECONDS` (default **120**, valid 1–600) with explicit
  validation, .env example and session-factory wiring. Connection/pool timeouts
  remain short (5 seconds), with 10 seconds for writing the request. Read timeout
  is per HTTP request, not an overall deadline or cancellation of server inference.
  This mitigates slow inference; it does not fix the observed GPU fault.
- Updated README and ENV_SETUP with the setting, diagnostics, measured findings,
  recovery boundaries and official NVIDIA/Ollama troubleshooting links.

### Live synthetic verification (no credentials/calendar data)

With approved loopback access, a direct inline HTTPX POST to `/api/chat` using
`qwen3:30b-a3b`, `Reply with OK.`, no thinking and 16 output tokens returned
**HTTP 200 in 17.15 seconds**. Reported load duration was about 13.90 seconds.
A second inline check called `OllamaProvider.chat` with the app's tool schemas
and a synthetic Example Person request: **passed in 41.23 seconds**, one tool
call. No Cal.com tool was executed. These successes do not establish GPU stability
or reproduce the user's exact private chat timeout/500. The earlier interrupted
synthetic process could no longer be polled; no result is claimed for that run.

### Automated verification

| Exact command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | Final run: **105 passed in 1.58s**. |
| `.venv/bin/python -m compileall -q app.py calcom_agent tests scripts` | Passed. |
| `git diff --check` | Passed. |

New tests cover timeout phase/limits/elapsed context, selected-model CPU metadata,
no repeated generation, server-error category filtering, invalid timeout values,
UI rendering of measured context, and removal of private fields. Existing factory
and 404 tests were updated for the timeout setting and additional metadata read.
All existing suites pass. No private `.env` was read or modified.

Live Cal.com checks, manual browser verification, GPU recovery/reboot and
post-recovery stability checks: **Not run**. After saving work, arrange the host
recovery indicated by its NVIDIA log; reconnect and verify `nvidia-smi` before
resuming model tests. If Xid 79 recurs under load, investigate driver/firmware,
PCIe, power and hardware causes without assuming this code fixes them.

## 2026-11-13 — Email validation: separate real-provider integration from offline workflow tests

### Root cause and supported settings

The affected write is **POST /v2/bookings**, using `cal-api-version: 2026-02-25`,
for both single creation and each occurrence of a weekly schedule. A real request
with the syntactically valid, reserved test address `demo@calendar-agent.invalid`
returned **HTTP 400**. The exact provider validation message was:

> This email address cannot receive mail. Please use a valid email.

The application maps this to `attendee_email_rejected`. It is a Cal.com server-side
deliverability failure, not evidence of malformed email syntax and not a calendar
creation success. Earlier `marketplace@g.service.com` troubleshooting also found
NXDOMAIN for its domain; changing the submitted address inside the client is not
a solution and is explicitly prohibited.

Supported API settings were checked against the current official documentation:
https://cal.com/docs/api-reference/v2/event-types/update-an-event-type
and https://cal.com/docs/api-reference/v2/bookings/create-a-booking .
`skipAttendeeEmailDeliverabilityCheck` skips the MX check but requires the editor
to belong to an organization; syntax checks and watchlist checks still apply.
This demo account reports no organization membership. The earlier supported-setting
attempt returned HTTP 400 for that eligibility restriction; no unsupported bypass
option was added or retried. Current live reads show both the 30-minute and
marketplace templates have `requiresBookerEmailVerification=false` and
`skipAttendeeEmailDeliverabilityCheck=false`. Thus disabling the booker email-code
verification requirement does not disable deliverability checking. That flag is
not an alternative MX bypass. Production eligibility checks were read-only.

### Minimal implementation

- Added `calcom_agent/mock.py`: explicit `MockCalcomAdapter` with a stateful
  `httpx.MockTransport`. It subclasses the real client so schemas, serialization,
  write error mapping, and fresh read-back verification remain exercised. It
  supports template reads/creation, slots, booking create/get/list, location update,
  reschedule and cancel. Synthetic domains are accepted when syntax is valid;
  no DNS lookup, provider request, email challenge or email sending occurs.
- The production session factory still constructs `CalcomClient`. There is no
  automatic fallback to mock after a live rejection, no environment-dependent
  address replacement, and no changes to user-provided email values. Offline
  Google verification remains explicitly unverified rather than simulated success.
- Added unit and scripted-agent workflows using `marketplace@g.service.com`,
  `person@example.test`, and mixed-case plus-tag addresses on `.invalid` domains.
  Tests assert exact attendee values in the outgoing POST and retained booking.
  A configurable mock rejection also verifies one rejected POST, no mutation,
  no retry, and no successful agent completion message.
- Added a narrowly scoped `prepare_update` tool/service operation for physical
  location updates, using Cal.com's supported PATCH `/v2/bookings/{uid}/location`
  with API version `2024-08-13`. It follows the existing prepare/confirm/execute
  architecture and verifies the updated location with a separate GET. Other
  booking fields are not implicitly editable. Google location sync is explicitly
  unverified because the existing Google verifier checks title/time, not location.
- Improved classification: the provider's “cannot receive mail” message is now a
  recognized deliverability reason. Generic “invalid email” is no longer mislabeled
  as a confirmed syntax failure without an explicit syntax/format indication.
- Added `scripts/check_email_integration.py`, an opt-in real-provider lifecycle
  runner requiring `--execute`, explicit attendee name/email, and optional
  `--check-rejection`. It never loads or selects the mock adapter. Live test identity
  was explicitly the account owner's controlled email; synthetic rejection used
  its original `.invalid` address, never replaced with that valid identity.

### Observed validation

- Offline suite: create, update (location), reschedule, cancel, get/list and full
  agent confirmation workflows pass with synthetic mail domains. Invalid email
  syntax remains rejected. Mock tests have no network transport.
- Live real Cal.com: create with a valid controlled address succeeded and was read
  back; the attendee email exactly matched the supplied value. Listing found the
  booking. Physical location update succeeded and was verified by GET. Reschedule
  succeeded and was verified. Cancellation succeeded and cleanup was verified by
  GET. No new template was needed for this lifecycle.
- Live negative test: reserved `.invalid` domain returned HTTP 400 with the exact
  message above; execution raised `attendee_email_rejected`, did not return a
  success result, and did not automatically retry. Additional diagnostic probes
  were separate developer-initiated requests, not application retries. One
  diagnostic response hook initially omitted `response.read()` and failed to read
  the HTTP 400 body; a corrected probe captured the message. No success is claimed
  for that failed diagnostic run.
- Automated command/results are recorded below after the final run. The existing
  live interactive runner is retained; the new runner additionally covers update
  and explicit rejection without replacing production tests with mocked tests.

### Remaining limits

A personal-account live call still cannot accept an undeliverable domain by
application fiat. Use controlled valid addresses for production integration tests
and the explicit offline adapter for synthetic-address challenge tests. The mock
is deterministic test infrastructure, not a complete emulation of Cal.com's
working hours, provider eligibility, recurrence engine or Google Calendar. No
email is silently substituted, discarded or normalized by the adapter.

Final automated results for this change:

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **168 passed in 1.22s** |
| `.venv/bin/python -m compileall -q calcom_agent scripts tests` | Passed |
| `git diff --check` | Passed |

Live invocation used `scripts.check_email_integration.run` with the explicitly
selected account-owner test identity and `check_rejection=True`; all five real
operations passed and the test booking was cancelled. To repeat using a designated
controlled mailbox, run from the repository root:

```sh
.venv/bin/python -m scripts.check_email_integration --execute \
  --attendee-name 'Demo Tester' --attendee-email 'YOUR_CONTROLLED_EMAIL' \
  --check-rejection
```

This command is intentionally opt-in and performs real writes. Do not use a
synthetic email for its positive lifecycle. To test arbitrary syntactically valid
addresses without provider access, explicitly inject `MockCalcomAdapter` as both
the conversation client and executor; no production configuration is changed.

## 2026-11-13 — Accepted live-email limitation; continue challenge work

The user confirmed that this provider restriction should be documented and that
work should continue. The live failure with `marketplace@g.service.com` is **not
resolved by the offline mock**: the Streamlit chatbot still uses real Cal.com,
which rejected the first occurrence with HTTP 400 `attendee_email_rejected`.
Zero of the 14 occurrences were verified; the other 13 were not attempted. The
existing marketplace template remains available for reuse.

The mock adapter is explicitly injected by offline unit/workflow tests and is not
currently selectable in the chatbot UI. There is no automatic real-to-mock
fallback and no silent attendee substitution. Passing synthetic-email tests must
not be described as enabling synthetic addresses in live calendar operations.
Real demonstrations require a valid controlled email; offline demonstrations can
use syntactically valid synthetic addresses without creating calendar events.
The current personal account cannot enable the organization-only deliverability
override. This is an accepted external limitation, not a claimed live fix.

README.md and ENV_SETUP.md now state the live UI/offline distinction prominently.
This follow-up changes documentation only; `git diff --check` was run. The prior
implementation verification remains 168 passing tests plus the successful real
valid-address lifecycle and explicit synthetic-address rejection check.

## 2026-11-13 — Fuzzy booking lookup before rescheduling

Problem: “reschedule the seminar to 4pm” could end with an unhelpful request for a
booking number instead of discovering the existing meeting. Added a read-only
`search_bookings` tool to the existing bounded agent loop. The LLM interprets
vague titles and relative dates into title terms plus absolute local time bounds;
Python applies those bounds and fuzzy token matching to API-returned bookings.
No user-language date parser was introduced. Missing structured bounds trigger
model correction rather than asking the user to repeat dates already provided.
Undated searches use a model-supplied next-90-day window. The clock context includes
an explicit Monday-based next-week anchor so the LLM need not calculate weekdays
unassisted.

Search supports title-only intent with a window, date/time-only intent, case
insensitivity and minor title typos. Cancelled/inactive and out-of-window bookings
are excluded. Up to 20 numbered candidates are retained; truncation is stated.
A successful search ends the turn with titles and original local times and asks
which meeting to change—even for a single result. It cannot silently proceed to
a reschedule. After the user chooses, the LLM preserves the requested target time
and selected meeting's date and calls the existing prepare/confirm path. The
service revalidates booking state/availability before execution. No-match responses
state the checked window and ask for title/date/original time rather than an
unknown booking number. A failed or empty search clears stale numbered references.

Tests cover fuzzy “seminor” matching Seminar, casing, unrelated titles, next-week
and original-time windows, following-week exclusion, cancelled entries, multiple
matches, no matches, model correction of omitted bounds, selection without writes,
and moving only the selected second meeting to 4pm after confirmation. The real
configured LLM was also checked against the offline adapter: it searched
2026-11-16T00:00:00 through 2026-11-23T00:00:00 for “next week's seminor,” displayed
two seminar matches, and asked which meeting to change. No real calendar call was
made in this model check, no pending write was prepared, and no POST followed the
search. Earlier probes exposed omitted bounds and a Sunday-based range; explicit
schema requirements, correction feedback and clock anchors fixed the observed run.

Scope remains Cal.com bookings. Search cannot find unrelated Google Calendar-only
events. Fuzzy matching is a candidate suggestion, not permission to pick or write.

Final validation: `.venv/bin/python -m pytest -q` — **182 passed in 1.26s**;
`compileall` and `git diff --check` passed. No live calendar writes were performed
for this lookup change.

## Issue tracking convention

At the user's request, record user-reported issues and their fixes in this file
as work proceeds. Include the observed behavior, root cause, implemented change,
validation, and unresolved limitations. Distinguish mocked tests, live read-only
checks, and live writes; do not describe an intermediate fix as resolving a
later-discovered root cause.

## 2026-11-13 — Restore requested Qwen3 30B A3B configuration

User question: is the current implementation using Qwen 30B A3B, and can it switch
to that model? The effective setting and project fallback were `qwen3:8b`, despite
an older progress entry describing a previous default change.

Changed Settings, the private local model setting, `.env.example`, the synthetic
Ollama check default, README.md, and ENV_SETUP.md to `qwen3:30b-a3b`. Confirmed the
effective setting and used a read-only local Ollama metadata request to verify
that this exact tag is installed. Configuration changes require a Streamlit
restart or new session. No credentials were logged.

Validation: **9 configuration tests passed** and `git diff --check` passed.
Live inference with the newly selected model was not run for this change.

## 2026-11-13 — Support booking duration updates without a new start time

User report: “can we change that seminar to 1 hour?” produced a claim that
`prepare_update` could not change duration, requested a new date/time, and showed
`invalid_tool_arguments`.

Root cause: `prepare_update` accepted only a physical address. The reschedule
schema required a start time, and scheduling always preserved the old duration.
The write adapter and read-back verifier also assumed the old reschedule length.

Added duration in minutes to `prepare_update` and `prepare_reschedule`. A
duration-only request keeps the saved booking's start time. Preparation validates
the template's allowed lengths and availability, excluding the booking itself;
confirmation refreshes state and checks availability again before writing.
Cal.com's documented reschedule endpoint receives `lengthInMinutes`, and
read-back verification checks the requested end time. Updated model instructions,
the offline adapter, and documentation. Physical-address updates remain supported;
address and duration changes require separate confirmations.

Validation: **193 tests passed**, including duration-only follow-ups, preserving
start time, unsupported lengths, revoking stale proposals, confirmation-time
conflicts, and detecting an API response that ignored the new length.
`git diff --check` passed. No live booking was changed. A requested duration must
be supported by the existing template; shared templates are not changed and
bookings are not automatically replaced.

## 2026-11-13 — Investigate duplicate Monday schedule entries

Initial user report: next Monday's listing displayed “1:1 with David” and
“David/Edward 1:1” as two adjacent meetings, while the user expected one.

First fix: deduplicate repeated API rows by booking UID, including overlapping
pagination pages, before assigning numbered references. Conflicting snapshots
for one UID produce a refresh error rather than silently selecting stale data.
Listings, searches, and scheduling refreshes share this behavior. Distinct UIDs
remain separate even if titles or times match. Tests verify stable references
and that a later confirmed change targets the correct booking.

Validation at this stage: **197 tests passed** and `git diff --check` passed.
This addressed repeated rows but did not resolve the user's actual discrepancy:
the two reported entries had different UIDs. The user's follow-up prompted live
read-only investigation rather than hiding similar titles.

## 2026-11-13 — Correct schedule timezone rendering

Follow-up report: the calendar showed only one Monday entry, so deduplicating
repeated IDs was insufficient. A live read found two Cal.com records marked
accepted. It also exposed incorrect timezone rendering: the model had relabelled
UTC timestamps as Pacific times, showing 17:00/17:30 instead of 09:00/09:30.

Schedule listing replies now render fetched titles, numbered references, and
converted timestamps directly in Python instead of trusting model-generated
listing prose. The tool result includes the display timezone. A regression test
supplies invented model prose and confirms only actual records and correctly
converted Pacific times appear.

Validation: **198 tests passed** and `git diff --check` passed. Live reads only;
no calendar writes. At this stage the one-versus-two discrepancy remained open.
The earlier statement that both Google events verified was incomplete: the
verification helper checked titles and times but did not check cancelled status.

## 2026-11-13 — Exclude cancelled Google events still active in Cal.com

User clarified that the visible calendar was Scheduling Demo. Further live
read-only inspection found the root cause: the Google event for the 09:30 David
booking was cancelled, while its Cal.com booking still said accepted. The 09:00
event remained active. The original verification incorrectly accepted the
cancelled Google event because its title and timestamps still matched.

Fixed Google verification so a cancelled event cannot verify an active booking.
Listings and searches check in-range active Cal.com bookings against linked
Google events and exclude confirmed cancelled Google events before assigning
numbers. Listing replies disclose exclusions caused by this state mismatch.
Missing references, failed reads, and other unverified results are not treated as
proof of cancellation. With multiple Google references, a cancelled reference is
reported as a mismatch rather than automatically hiding the booking, since
another reference may still be active.

Validation: **201 tests passed**, covering cancelled-event verification and
listing/search exclusion with stable references. `git diff --check` passed.
A live read through the corrected listing returned **one visible booking** on
Monday November 16: “1:1 with David,” **09:00–09:30 America/Los_Angeles**, with one
cancelled Google event excluded. No calendar records were changed. The underlying
Cal.com/Google status disagreement remains in the providers; the fix reconciles
the displayed listing and corrects verification rather than modifying records.

## 2026-11-13 — Readable local schedule dates and times

User report: the corrected schedule still displayed full ISO timestamps, repeated
calendar dates and UTC offsets, making a simple daily listing hard to read.

Changed schedule rendering to group bookings under readable local date headings
such as “Monday, November 16, 2026” and show compact 12-hour ranges such as
“9:00 AM–9:30 AM”. The schedule header names the configured IANA timezone once.
Overnight meetings include the end date; meetings crossing an offset change show
the timezone abbreviation at each endpoint. Numbered booking references remain
unchanged. Shortened the cancelled-event discrepancy note while retaining the
explanation that Cal.com still marks those records active.

Validation: **201 tests passed**, including the updated regression verifying
Pacific conversion, readable dates/times, and absence of ISO timestamps in the
listing. `git diff --check` passed. No live calendar calls or writes were needed
for this presentation change. Restart Streamlit to apply it.

## 2026-11-13 — Collapse model drafts and repair afternoon rescheduling

User report: “can we reschedule it to afternoon?” displayed a long unfinished
planning response, including a JSON example of an availability call, instead of
checking availability and offering times. User requested collapsed model-output
details rather than presenting that output as chat messages.

Root causes: output-limit responses were treated as completed answers; planning
prose was rendered directly; availability required separate template discovery
even when a saved booking already identified its event type; and model narration
could stall the response after a successful availability check.

Changes:
- Track Ollama `done_reason=length` and retry incomplete/planning-only generations
  within the existing bounded loop. Prose JSON is never executed as a tool call.
- Keep visible model drafts/tool commentary in a session-local, collapsed
  “Model output” expander attached to the reply, rather than separate messages.
  Reset clears drafts. Ollama's separate thinking field and inline `<think>`
  blocks are discarded, not forwarded or displayed.
- Include saved booking numbers, local start times, event type IDs and durations
  in model context. For afternoon moves, instructions preserve the booking's
  local date/duration and check 12:00–17:00 with its saved number.
- Availability can fetch the selected booking's template without unrelated
  discovery. It rejects a different event type, defaults to the saved duration,
  and passes the original booking UID to exclude self-conflicts.
- Render up to five actual returned rescheduling slots directly, with readable
  local times. Ask the user to choose before preparing a move; confirmation and
  fresh availability validation remain required before execution.

Validation: **205 tests passed**, including draft recovery, template resolution,
correct Pacific afternoon bounds, self-exclusion, duration preservation, event
mismatch rejection, confirmation gating, and collapsed UI details without extra
messages. The first live local-Qwen run against the synthetic adapter reached
availability but exhausted the loop afterward; this prompted direct rendering of
returned slots. No real calendar reads or writes occur in that model test.

A second synthetic Qwen probe showed that it omitted the booking number, so the
availability result was treated as a generic lookup and narration continued.
Added a read-only fallback for exactly one saved booking whose event type matches
the request: infer its reference for template resolution and self-exclusion.
Multiple saved bookings are never inferred, and write tools still require the
explicit saved number. Added regression coverage; **206 tests passed** and
`git diff --check` passed.

Final live local-Qwen probe against the synthetic adapter succeeded: it checked
availability and displayed five November 16 afternoon options starting at noon
in America/Los_Angeles, asked which time the user preferred, and left no pending
proposal and zero writes. Three intermediate draft outputs were retained only in
collapsed details. The scripted regression separately verifies selecting 2 PM,
preparing the move, and executing only after confirmation. No real booking was
moved, and live real-calendar rescheduling was not run for this fix.

## 2026-11-13 — Monday listing exhausted the model/tool loop

User report: “what's my schedule on next monday?” returned a scheduling-check
failure and `tool_limit`, with an instruction to narrow or reset a simple query.
The limit is six model iterations per turn, not six actual calendar writes:
planning-only responses and correction attempts consume the same budget.

Inspection found two bugs that can cause this symptom. Planning-repair system
messages were stored in persistent history, carrying move-specific instructions
into later listing requests. Also, a successful listing could still be followed
by incomplete model responses or repeated calls until the loop exhausted,
suppressing calendar data already fetched successfully. The user's UI snapshot
does not contain an exact request trace, so the precise six-step sequence of
that original turn cannot be established retrospectively.

Fix: remove temporary system correction messages when finishing a turn; make
correction guidance apply to the latest request and distinguish listings from
moves. Reuse the deterministic readable listing renderer immediately if the
model returns an unfinished/planning response after a successful listing, or if
the iteration budget ends with a successful listing as the latest observation.
Actual failed requests still report failure rather than treating stale data as
success. The six-iteration limit and confirmation gate remain intact.

Validation: **209 tests passed**, including successful listing on the last allowed
iteration, truncated generation after a successful read, and no correction-message
leak into the next turn. `git diff --check` passed. A live local-Qwen probe using
only the synthetic calendar returned Monday November 16, 9:00–9:30 AM Pacific,
with no diagnostics and two collapsed draft outputs. No real calendar calls or
writes were made by that probe. Restart Streamlit for the updated controller.

## 2026-11-13 — Practical live calendar acceptance checklist

User requested practical test cases in Calcom-README.md for booking, listing,
rescheduling, and cancellation, with verification that Google Calendar reflects
each change. Added a ten-step lifecycle plus twelve regression scenarios covering
local time rendering, relative dates, afternoon follow-ups, duration changes,
selection, confirmation/replay protection, similar titles, cancelled Google
status mismatches, loop recovery, and availability errors.

The checklist specifies a controlled real attendee, Scheduling Demo destination,
future dates, before-confirmation checks, manual Google verification after each
write, evidence/status recording, and cleanup. All cases start as Not run; mocked
suite results and earlier probes are not represented as executing this checklist.
Live test bookings and calendar writes were not performed for this documentation
request. Validation: reviewed against the current workflow and `git diff --check`.

## 2026-11-13 — Execute the live acceptance checklist (in progress)

User authorized live manipulation of Scheduling Demo and requested expected
responses for all 10 lifecycle and 12 regression cases. Added example outputs
for all 22 IDs to Calcom-README.md and created scripts/run_acceptance.py. The
runner uses the real configured Qwen model through ChatSession/Conversation,
restricts writes to its own test booking references, uses the account-owner test
mailbox, records redacted prompts/results, checks linked Google events, and
cleans up its fixtures. A dedicated temporary seminar template supplies the
30/60-minute prerequisite without editing existing shared templates.

Initial live lifecycle found HTTP rejection after valid proposals for moving and
cancelling. The original report is retained in
reports/live-acceptance-2026-11-13-core-initial.json. The test booking was cancelled
successfully by explicit cleanup with a nonempty reason. Fixed outgoing requests
to omit blank optional cancellation/rescheduling reasons; fixed reschedule
preparation to send lengthInMinutes only for multi-duration templates, since a
model may explicitly preserve 30 minutes even for a fixed-length template.
Added regression coverage for both request contracts; 212 offline tests passed.
Live retests and regression cases are continuing; this entry does not claim
those cases passed yet.

Google Calendar connection was offered via the plugin-management skill because
R10 requires a Google-only write. The current Cal.com integration can read linked
Google events, but a direct Google writer is not connected. R10 remains blocked
unless that connection becomes available. Other cases continue independently.

Live regression investigation found that local Ollama metadata for the installed
`qwen3:30b-a3b` advertises `thinking.values=[true]`: thinking cannot be disabled
for this installed alias. The previous forced `think=false` and 1,024-token
budget could return unfinished planning rather than tool calls. The adapter now
checks metadata once for this Qwen alias and uses its supported thinking mode
with a 16,384-token context and 4,096-token output budget when required. Private
thinking is still discarded. Other model defaults remain unchanged. The initial
regression run was interrupted during generation and its fixture/template cleanup
completed; initial results are preserved separately.

The new seminar fixture also exposed an overly restrictive title check: only the
1:1 custom naming pattern was accepted when a title booking field existed.
Preparation now also supports an event type whose configured static customName
exactly equals its event title; it does not accept arbitrary unconfigured titles.
Added coverage. Confirmation with no pending action is now answered directly,
without asking the model to recreate a proposal from history.

The acceptance runner additionally checks Google busy intervals on the specific
Scheduling Demo calendar, using a full local-day window. It records that the
interval was occupied while the test event was active, then verifies it clears
after cancellation. This is independent evidence of removal when cancellation
deletes Cal.com's event reference; a missing reference alone remains insufficient.
At this point the corrected live core run has passed C01–C06, including the
previously rejected confirmed reschedule and its Google read-back. Remaining
results are still in progress. Latest offline validation: 215 tests passed.

Further live results: the corrected core retest passed C01–C08, including Google
rescheduling, but cancellation with an omitted reason was still rejected by the
live account. Explicit fixture cleanup with a reason succeeded. Updated
prepare_cancel to preview the truthful default “User requested cancellation”
when the user supplies no reason; it is included in the confirmed payload.
This corrects the earlier incomplete assumption that omission alone resolved
cancellation. A dedicated regression test covers the preview and payload.

The corrected regression run passed R01, R02, R04 and R05 so far. R04 verified
that the new Google end time is one hour after the unchanged start. R03 initially
stopped at a redundant choice: after the user requested 4 PM and selected the
seminar, an exact 4 PM availability lookup was rendered as another “which time?”
question. Fixed the renderer to short-circuit only broad availability windows;
a narrow exact-slot check can continue to prepare_reschedule. Added coverage.
R03 will be retested separately. Current offline validation: 217 tests passed.

The main corrected regression run completed: R01/R02/R04/R05/R12 passed; R10 was
blocked for Google-only mutation, with the existing provider-status mismatch
checked read-only. R03/R06 exposed the same redundant exact-slot choice flow.
R07/R08/R09 failed during fixture setup because immediate reuse of the same slot
after cancellation could still be reported unavailable; they did not exercise
their intended action at that point. The runner now allocates fresh available
intervals instead of assuming a cancelled slot is immediately reusable.

R11 exposed a separate omission: Qwen left out the booking number while several
bookings were saved, even though only one matched the requested template. The
read-only fallback now resolves a unique template match among saved bookings;
it still refuses to infer among multiple matching bookings or for any write.
Booking/search/availability tool results now carry local timestamps, preventing
the model from relabelling returned UTC slots as evening local times. A regression
test verifies unique matching, self-exclusion, and local result timestamps.
All six affected regression IDs are being retested. Latest offline validation:
218 tests passed. All fixtures from the preceding run and its temporary seminar
template were cleaned up before the retest.

Affected-case retest update: R03, R06 and R07 have now passed live. Seminar
selection preserved 4 PM and produced a confirmation proposal; discard followed
by confirm produced no write; editing a pending move replaced the proposal and
only the revised time was applied. Linked Google read-back verified the applied
moves. R08/R09/R11 are still running. Added a negative regression ensuring no
read-only booking reference is inferred when two saved bookings share a template.
Latest offline validation: 219 tests passed.

R08 and R09 passed their live retests: duplicate confirmation and Streamlit AppTest
rerender caused no additional write, and two active same-title bookings remained
separate and matched their Google events. R11 then encountered Cal.com HTTP 429,
which also interrupted cleanup. Response headers showed a 120-request limit,
zero remaining, and an 11-second reset window. API traffic was paused; journalled
cleanup subsequently cancelled the one remaining test booking, verified its
Google interval clear, and deleted the temporary template. The report records
completed interrupted-run cleanup.

Added a 0.7-second minimum interval between the acceptance runner's Cal.com
requests to keep its repeated verification reads below the observed limit. This
pacing is confined to the test runner; no POST retry was added to the app. R11
is being rerun alone after cooldown, then the complete core lifecycle will be
rerun to confirm the cancellation default in sequence.

## 2026-11-13 — Live acceptance run complete except Google-only setup

Final results: **21 Pass, 0 Fail, 1 Blocked** across the requested 22 case IDs,
after retaining initial failures and applying the fixes described above.
- C01–C10: the final complete real-model/live-calendar run passed creation,
  confirmation, listing, afternoon choices, selected-time preparation, confirmed
  rescheduling, updated listing, cancellation proposal, confirmed cancellation,
  and final absence. C09 independently observed Google busy intervals clear.
- R01/R02/R04/R05/R12 passed in the main corrected regression run.
- R03/R06/R07/R08/R09 passed targeted retesting after the exact-time flow and
  fresh-fixture fixes. R08 included Streamlit AppTest rendering/rerendering with
  the real live session, rather than a manual browser check.
- R11 passed its final isolated retest after rate-limit cooldown and request
  pacing: listing → actual afternoon options → fresh listing, with no write or
  tool-limit error.
- R10 remains Blocked for deliberately cancelling a new event only in Google:
  no direct Google Calendar writer is connected. Its read-only check against the
  existing cancelled-Google/accepted-Cal.com mismatch passed. This is not counted
  as executing the blocked Google-only mutation, and no mock result substitutes
  for it.

All ten core and twelve regression expected-response examples are in
Calcom-README.md. Detailed results and source JSON links are in
reports/live-acceptance-2026-11-13-summary.md. Intermediate reports preserve
HTTP rejections, redundant choices, fixture setup failures and the rate-limited
run; the summary explicitly selects the latest completed result per case.
The CLI runner supports --only for targeted regression retests and paces its
Cal.com requests. No automatic POST retry was introduced.

Final independent cleanup audit examined 30 run-owned booking references: **zero
active test bookings, zero remaining temporary seminar templates, no test-time
busy intervals on Scheduling Demo**. The remaining Monday Google busy interval
is the pre-existing 09:00–09:30 America/Los_Angeles appointment. Existing user
appointments and the original provider-status mismatch were not modified.
Evidence: reports/live-acceptance-2026-11-13-cleanup.json.

Latest automated verification: **219 tests passed**; compileall and
`git diff --check` passed. Restart Streamlit to load the implementation fixes.
To finish R10, connect Google Calendar and rerun only the controlled Google-only
cancellation scenario using a newly created test fixture, then clean up that
fixture's Cal.com record through confirmation.

## 2026-11-13 — Retain Kevin's name across email/time follow-ups

User report: “book a meeting next Tue with Kevin” → “kevin@gmail.com” →
“1:00 PM–1:30 PM” produced “Please provide: attendee name,” even though Kevin
was in the original user message. The transcript remained in history, but the
model omitted name from its later prepare_create arguments. There was no
structured draft to supply that known field before validation.

Added a session-local new-booking draft and remember_booking_details tool. The
model records user-supplied name/email, resolved absolute date, duration and
timezone before clarification, updates them on follow-ups, and receives the draft
in system context separately from the transcript. Availability retains the chosen
template and returns readable local options without forcing premature creation.
prepare_create fills omitted or empty known attendee/template fields from the
draft when the user replies with only a selected time.

Names must appear as standalone user-supplied names, not be inferred from an
email address. A separate booking clears the draft. Changing attendee identity
removes the old email, including when a later prepare_create changes the name
without first updating the draft. Existing date/duration details can survive an
attendee correction, but another person's email is not silently inherited. Reset,
discard, and successful creation clear the draft. Preparation still performs no
calendar write and confirmation remains required.

Validation: **223 tests passed**, including the exact Kevin email/time sequence
with the model intentionally omitting name/email/template from preparation,
correct Tuesday 1 PM Pacific payload after confirmation, reset, no name inference
from email, and preventing stale email reuse for Alice. `compileall` and
`git diff --check` passed.

A real configured local Qwen probe against the synthetic calendar reproduced the
three user messages. It remembered Kevin, asked for the email, offered Tuesday
slots, then prepared Kevin's November 17 booking at 13:00 America/Los_Angeles with
the saved email, with no diagnostics and zero calendar POSTs. No invitation was
sent to Kevin and no real calendar was modified. This is separate from the
previous 22-case live run; those cases were not all rerun for this change.
Added the user-visible K01 follow-up regression and expected responses to
Calcom-README.md, and described the draft lifecycle in README.md. Restart
Streamlit to load the updated conversation controller.

## 2026-11-13 — Matt's Thursday request drifted to Monday; allow double booking

User report: “setup a 1:1 with Matt on next Thu 10:30” → “matt@gmail.com” produced
and executed a Monday November 16, 10:30 proposal instead of Thursday November 19.
The user also requested support for overlapping calendar bookings.

Date root cause: retaining attendee context alone did not enforce the original
weekday/date/time against a later prepare_create call. The system supplied a
Monday-based next-week anchor but lacked equivalent upcoming dates for the other
weekdays. A syntactically valid, available Monday could therefore pass scheduling
validation despite not matching the request.

Fix: remember_booking_details now records an independently supplied numeric
weekday (Monday=0), exact local HH:MM, and absolute date. It rejects an inconsistent
date/weekday. Preparation rejects a start differing from the saved date or exact
time, gives the model structured correction feedback, and never writes on that
mismatch. System context supplies upcoming dates for all seven weekdays. An
email-only reply cannot legitimately replace the saved Thursday or 10:30 time;
user-requested date/time edits must first update the draft. Added readable fixed
error codes for weekday/date and saved-time mismatches.

Overlap support: individual create/reschedule tools accept allow_conflicts, and
new-booking drafts can retain that preference across follow-ups. The model is
instructed that this user permits intended double booking and should preserve an
explicit requested busy time rather than silently move it. With the flag true,
preparation retains future-time, duration, supported-event and confirmation
validation, previews “Allow overlapping meetings,” and sends Cal.com's documented
allowConflicts=true on the supported write API version. The ordinary free-slot
requirement is bypassed for that explicit overlap proposal and again at
confirmation. No allowBookingOutOfBounds or skipBookingLimits flag is sent;
provider access and other scheduling rules remain enforced. Normal conflict-free
behavior is available with allow_conflicts=false. Calendar reads and listings
continue to keep distinct overlapping events separate.

Validation: **228 automated tests passed**, including deliberately wrong Monday
preparation followed by correction to Thursday, rejecting Monday labelled as
weekday Thursday, overlap flags on both create/reschedule confirmation paths,
and sending only the intended override to the HTTP endpoint. compileall and
`git diff --check` passed.

Configured local Qwen with a synthetic calendar reproduced the two Matt messages,
saved date=2026-11-19, weekday=3, time=10:30, and produced the correct Thursday
10:30 Pacific proposal with no calendar POST or invitation to Matt.

A controlled live Scheduling Demo test using the account-owner mailbox created
two test bookings at the exact same time, with the second using allowConflicts.
Both bookings and their distinct Google event references were verified at the
same start/end. Both test bookings were subsequently cancelled and the Google
interval verified clear. Evidence:
reports/overlap-verification-2026-11-13.json. No existing Matt or other real user
booking was moved, cancelled, or edited by these checks.

Added expected-response regressions K02 (Thursday carry-over) and K03 (overlap)
to Calcom-README.md and described the behavior in README.md. The earlier 22-case
live acceptance report remains historical and was not fully rerun for this change.
Restart Streamlit to load the fixes. The existing wrong-date Matt booking requires
a separately reviewed and confirmed reschedule if the user wants it corrected.

## 2026-11-13 — Exact-time draft must not become slot selection; warn after booking

User report: after “setup a 1:1 with Matt on next Thu 10:30” and the email reply,
the chatbot offered 10:00–10:30 and asked for a time again. User clarified the
overlap policy: book the requested time, then remind about any actual time
conflict and only suggest optional de-conflicting, without blocking double booking.

The earlier draft guards were insufficient: they rejected an incorrect final
prepare_create time but still allowed an availability result to short-circuit
into a new choice question. Also, whether overlap was permitted still depended
on the model supplying a flag.

Controller fix: an email-only reply updates the existing draft deterministically.
When name/email/date/exact time are known, it discovers/selects the supported
1:1 template and prepares that exact draft directly, without another model
interpretation. An accidental available_slots call for an exact-time draft also
routes to preparation instead of displaying alternatives. Missing identity or
template information requests only those fields, not another time. Broad requests
without an exact time retain the available-slot choice flow. Existing weekday,
date/time, attendee and confirmation validation still applies.

Policy fix: Conversation now defaults to allow_conflicts=True and explicitly
passes that policy to creation/rescheduling unless strict scheduling is requested.
The underlying SchedulingService retains its strict default for other callers.
The normal review/confirm gate remains, but no extra conflict-approval gate or
automatic time substitution occurs.

Added best-effort non-blocking conflict evidence to the write adapter. It checks
other active Cal.com bookings and selected/destination calendar busy intervals,
excluding the rescheduled booking and confirmed cancelled Google events. Failed
reads return unknown rather than preventing the write. Provider busy intervals
without event identity are treated cautiously when they might be the original
rescheduled event. After a verified write, detected conflicts produce:
“This meeting has a time conflict with another event. It is booked at your
requested time. You can ask me to suggest a different time.” Clear checks do not
produce a conflict warning; unknown checks are described as unverified. Neither
case triggers an automatic move, cancellation, or additional POST.

Validation: **232 tests passed**. Added coverage for exact-time draft routing,
email completion without another model call, default overlap permission,
post-success reminders, and successful writes despite failed conflict reads.
Legacy strict-availability tests now select strict policy explicitly. The prior
Matt drift test also verifies that email completion does not consume additional
model responses. compileall and `git diff --check` passed.

Configured local Qwen plus the synthetic calendar reproduced the Matt request:
it saved Thursday Nov 19 at 10:30, asked for the email, and the controller directly
prepared the saved time after the email reply. No invitation was sent to Matt.
A controlled live test then created two distinct overlapping Google events on
Scheduling Demo using the account-owner mailbox. The second write returned
conflicts.status=detected while succeeding. Both fixtures were cancelled and the
Google interval verified clear. Updated evidence:
reports/overlap-verification-2026-11-13.json; the earlier overlap report was
preserved as overlap-verification-2026-11-13-initial.json.

Documented updated expected behavior as K04 in Calcom-README.md and described
controller/policy behavior in README.md. Earlier opt-in overlap notes are
superseded for the chatbot by this default non-blocking policy. The original
22-case report remains historical; it was not entirely rerun for this follow-up.
Existing real Matt appointments were not modified. Restart Streamlit to load the
updated controller and write adapter.

## 2026-11-13 — Submission cleanup

User requested repository cleanup and updated ignore rules for submission.
Rewrote README.md as the current submission entry point: portable setup, examples,
behavior, architecture, offline verification, live-results scope and limitations.
Replaced the machine-specific environment guide with portable local/SSH setup
and concise troubleshooting. Kept Calcom-README.md's challenge and acceptance
cases and preserved this chronological issue/fix journal with a current status
header. Added project description/readme metadata in pyproject.toml.

Replaced the generic multi-framework .gitignore with project-specific exclusions
for private environment files, Streamlit secrets, local credentials/agent/editor
state, Python environments/build/cache outputs, logs and raw live-test JSON or
recovery journals. .env.example, requirements.lock, .streamlit/config.toml and
the curated Markdown results remain submission candidates. Raw reports are
retained locally, not deleted; they contain live calendar references. Sanitized
the public results summary and removed Markdown links to excluded raw evidence.
Historical journal references to local raw evidence remain as history.

The live acceptance CLI now generates unique timestamped report/journal filenames
rather than hardcoded dated filenames or a shared /tmp journal. Both reports and
journals are created with private file permissions. Invalid regression IDs or
--only combined with the core suite are rejected before initialization/network
access. The historical unavailable-time regression explicitly selects strict
scheduling, since the chatbot's current default permits overlaps.

Validation: **232 tests passed**. Configuration TOML parsed successfully, ignore
rules include/exclude the intended files, Markdown file links resolve to included
submission files, and invalid runner invocations exit during argument parsing.
Scanned submission candidates for credential-like strings/private keys and local
machine paths: none found. compileall and git diff --check passed. Removed
repository-generated Python/pytest caches after verification; local .env and
.venv remain intact and ignored. No live calendar operations were performed for
this cleanup.

## 2026-11-13 — Reviewer reproducibility audit

User asked whether the currently Git-tracked files are sufficient for a reviewer.
They are not yet: git ls-files contains only .gitignore, Calcom-README.md, LICENSE
and README.md. The implementation, dependency declarations/lock, config example,
tests and supporting documentation remain untracked. A clone of the current
commit therefore does not include the runnable app. The tracked Markdown files
also have uncommitted working-tree changes. No index or commit was changed by
this audit.

Tested the intended submission separately by copying only tracked plus non-ignored
untracked files into a fresh /tmp directory. The copy excludes .env, the existing
virtualenv, private agent/editor state and raw live JSON reports. Created a new
Python 3.12 environment and installed all 41 locked packages using the documented
uv alternative with a fresh temporary cache. Dependency compatibility check
passed, and **232 tests passed in the isolated copy**. Streamlit AppTest without
credentials produced configuration guidance without an application exception.
No Ollama or real calendar connection was used for those offline checks.

Reviewer prerequisites for live behavior remain documented: their own Cal.com
credentials/connected calendar and an installed Ollama tool-capable model.
Remaining submission action: add and commit the intended non-ignored files.
The raw private artifacts stay excluded under the verified ignore rules. This
is an audit result, not a claim that the current Git commit already contains the
implementation.

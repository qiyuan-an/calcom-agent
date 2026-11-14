# Cal.com conversational scheduling assistant

A Python/Streamlit chatbot for creating, listing, rescheduling, and cancelling
Cal.com meetings through natural conversation. It uses a local Ollama model for
language interpretation and validates structured actions in Python. Calendar
writes require a reviewed proposal followed by an explicit confirmation.

## Quick start

Requirements: Python 3.11+, Ollama, and a Cal.com account with an API key.
Connect Google Calendar to Cal.com if you want bookings written to Google.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
if [ ! -e .env ]; then
    (umask 077; cp .env.example .env)
fi
chmod 600 .env
```

Fill in your private `.env`:

```dotenv
CAL_API_KEY=your-calcom-api-key
CAL_USERNAME=your-public-calcom-username
OLLAMA_MODEL=qwen3:30b-a3b
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_TIMEOUT_SECONDS=120
DEFAULT_TIMEZONE=America/Los_Angeles
```

Start Ollama if it is not running, download the configured model if needed,
then start the app from the repository root:

```sh
ollama serve
# In another terminal:
ollama pull qwen3:30b-a3b
.venv/bin/python -m streamlit run app.py
```

Open <http://localhost:8501>. If Ollama is already running, reuse that daemon.
The 30B model requires substantial memory; `OLLAMA_MODEL` can select another
installed tool-capable model. The checked-in dependency lock was verified on
Ubuntu with Python 3.12. See [environment setup](ENV_SETUP.md) for configuration,
Cal.com/Google setup, remote access, and troubleshooting.

## Example workflow

1. “Set up a 1:1 with Matt next Thursday at 10:30.”
2. Supply the attendee's email when asked.
3. Review the proposed attendee, local date/time, and duration.
4. Reply “confirm” to create the meeting.
5. “What's my schedule next Thursday?”
6. “Move the Matt meeting to the afternoon.” Choose an offered time and confirm.
7. “Cancel that meeting.” Review and confirm the cancellation.

Use a mailbox you control for testing: real bookings may send invitations and
calendar updates. A name/email/date/time draft preserves details across short
follow-ups. Exact requested times remain attached to that draft; an email reply
completes the proposal rather than asking the user to choose a time again.

## Behavior

- Listings use fetched Cal.com records, stable numbered references, and readable
  local times. Repeated rows are deduplicated by booking UID; distinct meetings
  remain separate even when titles or times match.
- Title/date searches help identify existing bookings before changing them.
  Broad requests such as “afternoon” offer actual available slots.
- Create, move, cancel, and duration-change proposals are validated before
  execution. Edits revoke an earlier proposal; confirmation tokens expire and
  cannot be replayed. Availability/state are rechecked where applicable.
- The chatbot permits overlapping meetings by default. It preserves the requested
  time, requires the usual confirmation, and reports a detected conflict after
  a verified booking with an optional suggestion to choose another time. It does
  not move either meeting automatically. Conflict-check failures do not block
  booking and are reported as unverified. Strict scheduling can be selected by
  callers with `allow_conflicts=False`.
- Cal.com writes are read back before success is reported. Google synchronization
  is reported separately; missing references alone do not prove deletion.
  Confirmed cancelled Google events still marked active in Cal.com are excluded
  from schedule listings with an explanatory note.
- Intermediate model drafts are collapsed under **Model output**. Private model
  thinking is discarded. Thinking-only Qwen metadata is respected rather than
  forcing an unsupported generation mode.

## Implementation

```text
Streamlit UI → ChatSession → Conversation → Ollama
                                │
                      SchedulingService
                                │ confirmed actions
                           CalcomClient → Cal.com → Google Calendar
```

| Component | Responsibility |
| --- | --- |
| `app.py`, `ui_session.py` | Session-owned chat state, submission deduplication, rendering and diagnostics |
| `conversation.py`, `model.py`, `ollama.py` | Bounded model/tool loop, validated tool arguments, booking drafts and provider boundary |
| `scheduling.py` | Deterministic preparation, validation and confirmation gate |
| `client.py` | Versioned HTTP contracts, sanitized failures and write/read-back verification |
| `lookup.py`, `series.py` | Booking search and bounded weekly-series preparation/execution |
| `mock.py`, `tests/` | Explicit offline calendar adapter and automated regression tests |

Credentials remain in the HTTP client, outside model messages. Environment
loading is explicit; no global client or shared cross-user conversation is cached.
There is no automatic POST retry or live-to-mock fallback.

## Verification

Run offline tests without credentials, Ollama, or real calendar access:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q calcom_agent scripts tests
```

Latest implementation checks: **232 tests passed**. Tests cover API contracts,
timezones, confirmation/replay behavior, follow-up context, overlapping meetings,
provider failures, and Streamlit interactions.

[Calcom-README.md](Calcom-README.md) contains the original challenge and practical
acceptance cases with expected responses. The historical live run had **21 passes
and one blocked Google-only mutation case**, followed by focused context/overlap
regressions. See the [sanitized live results](reports/live-acceptance-2026-11-13-summary.md).
That report distinguishes live checks from synthetic tests and is not a claim
that every historical case was rerun after each later change.

Optional runners are in `scripts/`. Use `--help` before executing them. Live
runners require explicit execution/confirmation and may create invitations;
raw reports and recovery journals are ignored by Git. The synthetic Ollama check
contacts the configured local model with fake calendar data only:

```sh
.venv/bin/python -m scripts.check_ollama --model qwen3:30b-a3b
```

## Scope and limitations

The assistant manages Cal.com bookings; it is not a general Google Calendar editor
and does not list unrelated Google-only events. Standard personal event types
are the primary supported workflow. Paid, seated, and native recurring event
types are rejected; the optional weekly workflow creates individual bookings.
Duration changes require a supported event-type length. Calendar/provider access,
working hours, and email deliverability rules still apply even when overlaps are
allowed. Unknown write outcomes require inspection before retrying.

Local session state is in memory and does not survive server restarts. The app
assumes a trusted user and binds to localhost by default; use an SSH tunnel for
remote development rather than exposing an unauthenticated public server.

[progress.md](progress.md) preserves the development issue/fix history, including
intermediate failures and validation limits. [LICENSE](LICENSE) contains licensing
terms.

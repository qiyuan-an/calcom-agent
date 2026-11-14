# Environment setup

Run commands from the repository root. The app and Ollama run on the same host;
the browser can connect locally or through an SSH tunnel.

## Python dependencies

Python 3.11+ is required. The pinned environment was checked on Ubuntu/Python 3.12.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

Alternatively, with `uv` installed:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock
```

`pyproject.toml` declares direct dependencies; `requirements.lock` pins runtime
and test dependencies. Regenerate the lock intentionally when changing them:

```sh
uv pip compile pyproject.toml --extra test -o requirements.lock
```

## Private configuration

Create `.env` only if it does not already exist:

```sh
if [ ! -e .env ]; then
    (umask 077; cp .env.example .env)
fi
chmod 600 .env
```

| Setting | Meaning |
| --- | --- |
| `CAL_API_KEY` | Cal.com API key for the account whose bookings you manage |
| `CAL_USERNAME` | Bare public Cal.com username, not an email or booking URL |
| `OLLAMA_MODEL` | Installed model tag; default `qwen3:30b-a3b` |
| `OLLAMA_BASE_URL` | Ollama HTTP endpoint; default `http://127.0.0.1:11434` |
| `OLLAMA_TIMEOUT_SECONDS` | Per-request model read timeout, 1–600 seconds; default 120 |
| `DEFAULT_TIMEZONE` | IANA timezone; default `America/Los_Angeles` |

Process environment variables override `.env`. Restart Streamlit after changing
configuration. `.env`, Streamlit secrets, and raw live-test artifacts must remain
local; `.env.example` and `.streamlit/config.toml` are intended for submission.

## Ollama

```sh
ollama serve
```

Use one daemon; address-in-use usually means Ollama is already running. In
another terminal:

```sh
ollama pull qwen3:30b-a3b
ollama list
```

The 30B model has substantial RAM/VRAM requirements. You can select another
installed tool-capable model through `OLLAMA_MODEL`; behavior should be retested
with that model. The adapter checks metadata for the default Qwen alias and
supports its thinking-only variant while discarding private thinking output.

## Cal.com and Google Calendar

1. Generate a Cal.com API key and set the matching public username in `.env`.
2. Connect Google Calendar in Cal.com and create or select the intended calendar.
   For the documented live checklist, use a dedicated **Scheduling Demo** calendar.
3. Set the destination under Cal.com's **Add to calendar**, and the calendars
   used for conflict checks. Check any event-specific destination override too.
4. Configure a free personal event type, availability hours, timezone, and one
   supported meeting location such as Cal Video.
5. For 1:1 naming, use the supported pattern
   `{Scheduler first name}/{Organiser first name} 1:1`. For a static seminar title,
   configure the event type's custom name to match its title.
6. To test duration changes, configure an event type offering the requested
   lengths. Existing fixed-duration templates cannot be stretched arbitrarily.

Use a real controlled attendee mailbox for live testing. Cal.com can reject
synthetic or undeliverable email addresses even when their syntax is valid.
A Google integration and a meeting location are separate settings. The app uses
Cal.com's integration; a separate Google connector is needed only for tests that
intentionally edit Google independently of Cal.com.

## Run locally or over SSH

```sh
.venv/bin/python -m streamlit run app.py
```

The checked-in Streamlit config binds to `127.0.0.1:8501` and disables usage
telemetry and raw error details. For a remote host, open this tunnel on your
laptop, substituting your own SSH destination:

```sh
ssh -N -L 8501:127.0.0.1:8501 user@your-server
```

Open <http://localhost:8501> in the laptop browser. Ollama does not need a public
port. The app has no public multi-user authentication layer.

## Checks and troubleshooting

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q calcom_agent scripts tests
```

The offline suite needs no API credentials and sends no invitations. For live
checks, follow [the acceptance checklist](Calcom-README.md) and inspect runner
`--help` first. Use only controlled fixtures; inspect unknown write outcomes
before retrying. Raw JSON reports/journals are ignored because they may contain
calendar references and private event details.

| Symptom | Check |
| --- | --- |
| Missing settings | Fill `.env`; confirm the process environment is not overriding it |
| Model unavailable | Start Ollama, verify the endpoint and exact installed tag; inspect **Model connection** |
| Model timeout | Inspect memory/GPU placement and choose a suitable model or timeout; timeout is not proof of GPU health |
| Template lookup fails | Confirm API key and public username belong to the same account |
| Email rejected | Use a controlled deliverable mailbox; account/provider policy cannot be bypassed by the mock adapter |
| Google sync unverified | Check destination/integration, then inspect Google; do not recreate the booking blindly |
| Rate limited | Wait for the provider's retry window; do not repeatedly confirm or rerun writes |
| Details forgotten after restart | Browser/server session state is in memory; begin a new request after a restart |

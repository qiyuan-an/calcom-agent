# Conversational scheduling assistant coding challenge

## Overview

Meet your user: a busy founder who lives in their inbox and runs their day out of cal.com. They don't want forms or
menus — they just want to type things like "book a 30-min intro with a candidate Thursday afternoon", "what's on my
calendar tomorrow?", or "move my 3pm to later today" and have it handled.

Your task is to build an interactive chatbot that lets this user manage their cal.com account through plain conversation.

How you build the conversational layer is up to you. You are free to use any LLM provider.

## Requirements

Build a simple chatbot that can interact with the cal.com REST API. Through the chat interface, the user should be able to:

 - Book a new event (the assistant gathers whatever details it needs, then creates the event).
 - See their scheduled events.
 - Cancel an event.
 - Reschedule an event they've booked.

It's a plus if the chatbot is usable through an interactive web UI.

Build the experience you'd want this user to have.

### Language

Please use Python for this code challenge.

## Cal.com API Reference

First, you'll need to create a cal.com account and obtain an API key. Follow the instructions in the
[authentication document](https://cal.com/docs/enterprise-features/api/authentication) to get started.

Second, here is the documentation for the cal.com [booking API](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings) and [slot api](https://cal.com/docs/api-reference/v2/slots/find-out-when-is-an-event-type-ready-to-be-booked).

## Practical end-to-end test plan

Use these cases to test the chatbot against real Cal.com bookings and the Google
**Scheduling Demo** calendar. Automated tests use mocked calendar responses;
passing them does not establish that real invitations or Google changes work.

### Test setup

- Restart Streamlit after configuration or code changes. Confirm the selected
  model is `qwen3:30b-a3b` and the displayed timezone is `America/Los_Angeles`.
- In Cal.com, verify **Scheduling Demo** is the destination calendar, including
  any event-type override. In Google Calendar, select that calendar and use the
  same timezone. Record the calendar's existing entries before testing.
- Use a real email address you control as the test attendee. Replace `[email]`
  below with that address; synthetic domains can be rejected by live Cal.com.
- Replace `[date]` with a future weekday and `[time]` with an available local time.
  Use dates with open morning and afternoon slots. For relative-date tests, note
  today's local date and the expected next Monday before submitting the prompt.
- Use a distinctive attendee name, such as `Calendar Test`, to identify test
  bookings. Record each returned booking reference; a reschedule may return a
  new reference. Never cancel unrelated calendar entries as cleanup.
- For the duration test, choose a template that explicitly supports both 30 and
  60 minutes. A fixed 30-minute template is the negative test, not a reason to
  modify a shared template automatically.

### Core lifecycle: run in order

Keep the same chat session so follow-up references such as “it” retain context.
Check both systems after each confirmed change before moving to the next case.

| ID | Chat input / action | Expected chatbot and Cal.com behavior | Google Scheduling Demo check |
| --- | --- | --- | --- |
| C01 | “Book a 30-minute meeting with Calendar Test on [date] at [time]. Their email is [email].” | Check availability; gather any genuinely missing details; display a proposal. No booking exists before confirmation. | No new event before confirmation. |
| C02 | Reply “confirm” to C01. | Execute once; return the booking reference and verification results. Distinguish Cal.com success from Google sync verification. | Exactly one active event at the proposed local date/time, lasting 30 minutes. Check title, attendee, and destination calendar. |
| C03 | “What's my schedule on [date]?” | Show the new booking exactly once, with readable local times and a numbered reference. Do not invent entries from earlier messages. | Listed title and time match the visible event. Existing unrelated bookings remain visible as appropriate. |
| C04 | After C03: “Can we reschedule it to afternoon?” | Preserve the booking's date and duration; check actual slots between noon and 5 PM; offer choices. No move or confirmation proposal until a specific time is chosen. | Original event remains at its original time. No extra afternoon event appears. |
| C05 | Choose one returned time: “Move it to [offered time].” | Present a move proposal for the selected booking and date. | Original event remains unchanged before confirmation. |
| C06 | Reply “confirm” to C05. | Execute once and verify the new start/end. Save the new reference if it changes. | One active meeting at the chosen afternoon time. The old time has no active copy. Check title, attendee, and duration were preserved. |
| C07 | “What's my schedule on [date]?” | Show the moved booking once at the new time; omit cancelled or superseded copies. | Listing agrees with the updated calendar. |
| C08 | “Cancel that meeting.” | Identify the saved meeting and display a cancellation proposal. | Event remains active before confirmation. |
| C09 | Reply “confirm” to C08. | Cancel once; report Cal.com verification and the actual Google verification status. A missing booking reference alone is not proof of Google deletion. | Event is removed or marked cancelled, not active. No unrelated event changes. |
| C10 | “What's my schedule on [date]?” | The cancelled test booking is absent. Other bookings remain. An empty Cal.com list must not claim all Google calendars are empty. | No active copy of the test meeting remains. |

### Regression and follow-up cases

Use separate test bookings when needed. These cases reproduce the issues found
while debugging rather than relying only on the happy path.

| ID | Chat input / action | Expected result | Google Calendar check |
| --- | --- | --- | --- |
| R01 | “What's my schedule next Monday?” | Resolve the correct Monday in the local timezone. For example, from Friday Nov 13, 2026, use Monday Nov 16. Show friendly dates and times, not raw ISO timestamps. | Compare the same local day. A 17:00 UTC booking on that example date must appear at 9:00 AM Pacific, not 5:00 PM. |
| R02 | After a listing: “Move the first meeting to [specific time].” | Use the saved numbered reference, preserve its local date and duration, and require confirmation. Do not demand a UID or unrelated template discovery. | Only the selected meeting moves after confirmation. |
| R03 | “Reschedule next week's seminar to 4 PM.” Then choose a returned meeting. | Search the next Monday-through-Sunday window; show matches and ask which one. Preserve the requested 4 PM target across selection. | No change during search/selection; after confirmation, only the chosen seminar moves. |
| R04 | With one booking selected: “Make it 1 hour.” | Preserve its start time; validate a supported 60-minute duration and availability; propose the change. No request for a new date/time. | Before confirmation, unchanged. After confirmation, one event at the same start with an end one hour later. |
| R05 | Repeat R04 using a fixed 30-minute template, or extend into a known conflict. | Explain unsupported duration or unavailable time. Do not silently change templates, replace the booking, or claim success. | Original event and duration remain unchanged. |
| R06 | Prepare a move, then say “Never mind.” Then say “confirm.” | Discard the proposal; the later confirmation cannot execute it. | No change. |
| R07 | Prepare a move, then request a different time before confirming. | Revoke the old proposal; show the revised proposal. Confirmation applies only to the latest validated choice. | Exactly one move to the revised time, with no intermediate active copy. |
| R08 | Confirm an action, then send “confirm” again and refresh the browser. | No replay or duplicate write. | Exactly one create/move/cancel effect. |
| R09 | Ask for a date containing two separate meetings with the same attendee or similar titles. | Keep distinct active bookings; do not merge based on title similarity or adjacent times. Numbered references target the intended booking. | Each listed active meeting corresponds to a separate actual event. |
| R10 | Controlled test: cancel one test event directly in Google while its Cal.com booking remains accepted, then list that day. | Exclude the confirmed cancelled Google event and explain the status mismatch. Keep the active test event. Failed reads or missing references must not be treated as cancellation. | Listing matches active visible events. Record this deliberate mismatch for cleanup in Cal.com. |
| R11 | Ask for Monday's schedule, request an afternoon move, then ask for Monday's schedule again. | Each new request uses its own intent. No stale move instructions, planning monologue, or avoidable `tool_limit` failure. Unfinished visible model output is under collapsed **Model output**, not the answer. | Listings agree with the current calendar; no move occurs without confirmation. |
| R12 | Request an unavailable time or a date with no Cal.com bookings. | Offer checked alternatives or accurately describe the checked range. Do not infer availability from an empty booking list. | No conflicting event is created and no existing event changes. |

For R10, deliberately changing Google does not repair the Cal.com record. After
recording the result, separately prepare and confirm cleanup of that specific
Cal.com test booking. Do not perform this test on a real appointment.

### How to record results

Mark each case **Not run**, **Pass**, **Fail**, or **Blocked**. Each new run
starts as **Not run**. The live-results section below records actual execution;
example outputs alone are not evidence of a pass.

For each run, record:

| Field | Record |
| --- | --- |
| Case / status | Case ID and Pass, Fail, or Blocked |
| Environment | Date, local timezone, selected model, destination calendar |
| Inputs | Exact prompts and confirmation sequence; redact attendee email in shared notes |
| Before / after | Local start/end, duration, title, and number of active test events |
| Evidence | Cal.com reference and verification outcome; manual Google inspection and, if useful, a screenshot kept privately |
| Failure details | Diagnostic stage/code, collapsed output summary, and whether any write may have happened |
| Cleanup | Which test bookings were removed and whether both systems agree |

Refresh Google Calendar after each write. If it has not updated, wait briefly and
check again, recording the delay. Do not repeatedly confirm or recreate a meeting
to force sync. A timeout or unknown write outcome requires inspecting both systems
before any retry. Mark the case Fail or Blocked if they still disagree; do not
record Pass merely because the assistant claimed success.

Document newly discovered issues, their fixes, and retest evidence in
[progress.md](progress.md). At the end, cancel any remaining test bookings through
the chatbot's confirmation flow and verify there are no active test events left
in Scheduling Demo.

### Example expected chatbot output for every case

These examples define the user-visible result, not exact wording assertions.
Replace bracketed values with the selected date, actual offered times and returned
reference. Titles may follow the selected template's naming pattern. Proposals
must name the exact local date/time and duration and ask for confirmation.

| Case | Example expected output |
| --- | --- |
| C01 | “Create Calendar Test/Edward 1:1 on [date], 9:00–9:30 AM Pacific with Calendar Test ([email]). Reply ‘confirm’ to execute.” |
| C02 | “Booking created and verified in Cal.com. [Reference]. Google Calendar change verified.” If Google cannot be read back, say “Google Calendar sync is not verified”; do not claim full success. |
| C03 | “Your Cal.com schedule (America/Los_Angeles): [weekday, month day, year]. 1. Calendar Test/Edward 1:1 — 9:00 AM–9:30 AM.” |
| C04 | “Here are available slots (America/Los_Angeles): [date] 12:00–12:30 PM; 12:30–1:00 PM; … Which time would you prefer? I’ll prepare the move for confirmation.” If none exist, state that instead. |
| C05 | “Move Calendar Test/Edward 1:1 on [date] from 9:00 AM to [chosen time], 30 minutes. Reply ‘confirm’ to execute.” |
| C06 | “Reschedule verified in Cal.com. Start: [chosen local time]. Duration: 30 minutes. [New reference]. Google Calendar change verified.” An unverifiable Google result must be explicit. |
| C07 | “1. Calendar Test/Edward 1:1 — [new local start]–[new local end].” No active entry at the old time. |
| C08 | “Cancel Calendar Test/Edward 1:1 on [date] at [current time]. Reply ‘confirm’ to execute.” |
| C09 | “Cancellation verified in Cal.com. Status: cancelled.” Follow with the actual Google verification result; if the reference was removed, do not claim this proves deletion. |
| C10 | If this was the only booking: “No Cal.com bookings were found in the checked date range.” Include the scope limitation; otherwise list only the remaining active bookings. |
| R01 | “Monday, November 16, 2026. 1. [actual title] — 9:00 AM–9:30 AM. America/Los_Angeles.” Use the actual next Monday for the run. |
| R02 | “Move [first saved title] on [same local date] to [requested time], [unchanged duration] minutes. Reply ‘confirm’ to execute.” |
| R03 | “I found these Cal.com bookings: 1. [seminar and local date/time]; 2. … Which meeting should I change?” After selection: “Move [selected seminar] to 4:00 PM on [its date]. Reply ‘confirm’ to execute.” |
| R04 | “Update [selected title]: same start [date/time], duration 60 minutes, ending [one hour later]. Reply ‘confirm’ to execute.” After confirmation: verified new end time. |
| R05 | “This template does not support the requested duration” or “That time is unavailable.” No success claim, no pending replacement booking, and no template edits. |
| R06 | “Pending action discarded. No booking was changed.” A later “confirm” must say there is no pending action, rather than execute the discarded move. |
| R07 | “Move [title] to [revised date/time]. Reply ‘confirm’ to execute.” The revised proposal replaces the old one. |
| R08 | First confirmation reports the verified result; a second says “There is no pending action to confirm.” Refresh must not produce another mutation. |
| R09 | “1. [title] — 9:00 AM–9:30 AM. 2. [similar title] — 9:30 AM–10:00 AM.” Keep both when both are active distinct events. |
| R10 | List the remaining active test event only, followed by “Cancelled Google Calendar events are hidden; Cal.com still marks them active.” |
| R11 | Return a readable Monday listing, actual afternoon options, then a fresh readable Monday listing. Drafts stay collapsed; no `tool_limit` error and no unconfirmed move. |
| R12 | Conflict: “No available slots were found in that window. Shall I check another time? No booking was changed.” Empty day: “No Cal.com bookings were found in the checked date range”; do not equate this with calendar availability. |

Actual live results, including failures and unavailable prerequisites, are recorded
below and in progress.md. Expected outputs are acceptance criteria, not claims
that these outcomes have already been observed.

### Running the live checklist with the configured model

The live runner uses the same `ChatSession` and `Conversation` path as Streamlit,
the configured local Ollama model, real Cal.com writes, and linked Google event
reads. It sends invitations to the account owner's mailbox and automatically
submits the test confirmations. It restricts writes to bookings created by its
own run and cleans up its fixtures. Regression prerequisites are prepared through
the real scheduling service; that setup is separate from each conversational
assertion. R08 also renders and rerenders the live session with Streamlit AppTest.

```sh
.venv/bin/python -m scripts.run_acceptance --execute --suite core
.venv/bin/python -m scripts.run_acceptance --execute --suite regressions
```

Run the suites sequentially. They require Scheduling Demo as the default
destination and free Monday morning/afternoon slots. The regression suite creates
and removes its own 30/60-minute seminar template. Local-only JSON reports under `reports/`
contain redacted inputs, actual replies, diagnostics, write outcomes and Google
read-back results. An unknown write outcome stops the runner without retrying.

Google verification uses linked event title/start/end/status plus its destination
calendar ID. Where cancellation removes the event reference, the runner can
independently check that an interval previously observed busy in Scheduling Demo
becomes clear. This establishes absence of an active busy event in that interval;
it does not claim that a missing reference proves deletion or inspect an event's
trash history. R10 needs a separately connected Google Calendar writer to perform
the deliberate Google-only cancellation. Unsupported prerequisites are recorded
as Blocked, not silently replaced with mocked results.

### Live results — November 13, 2026

**21 passed, 1 blocked** after fixes and targeted retests: all ten core lifecycle
cases and eleven regression cases passed with the real configured Qwen model,
Cal.com, and Scheduling Demo. Test appointments used Monday November 16 in
America/Los_Angeles. R10's existing-mismatch read-only check passed, but its
Google-only cancellation setup remains blocked until Google Calendar is directly
connected.

See the [sanitized case-by-case results](reports/live-acceptance-2026-11-13-summary.md).
Initial failures are retained rather than overwritten as passes. Running these
cases found and fixed model thinking configuration, mutation payloads, seminar
title handling, redundant time selection, local timestamps, and reference recovery.
The runner now paces requests after a rate-limit interruption.

Final provider read-back checked
30 run-owned booking references: **zero active test bookings, zero temporary test
templates, and no remaining test-time busy intervals**. Existing user appointments were unchanged. Cancellation checks used the observed Google
busy interval clearing when an event reference was removed; they did not infer
Google deletion from a missing reference alone. No unrelated appointments were
changed.

### Booking context regression — K01

Use this follow-up sequence to check that a new-booking draft retains details:

| Step | Input | Expected response |
| --- | --- | --- |
| 1 | “Book a meeting next Tue with Kevin.” | Remember Kevin and the resolved Tuesday; ask for Kevin's email. Default to 30 minutes. |
| 2 | Supply a controlled attendee email. | Retain Kevin and Tuesday; offer actual available local times and ask which time to use. |
| 3 | Choose an offered range, e.g. “1:00 PM–1:30 PM.” | Prepare Kevin's booking for that Tuesday at 1 PM using the saved email. Do not ask for the attendee name again. |
| 4 | Review, then confirm only if running a real controlled test. | Create exactly one booking with the saved attendee, date and duration. Verify Google and clean up as in C02/C09. |

No event should be created during steps 1–3. Reset, “Never mind,” successful
creation, or an explicit separate booking clears the draft as appropriate. A new
attendee must not inherit the previous person's email. Names must come from user
messages; do not infer them from email addresses. This regression was added after
the original 22-case live run and is reported separately from those results.

### Date carry-over and overlapping meetings — K02 / K03

| Case | Conversation / setup | Expected output and calendar result |
| --- | --- | --- |
| K02 | From Friday Nov 13, 2026: “Set up a 1:1 with Matt next Thu at 10:30,” then supply a controlled test email. | Ask only for the email, then propose **Thursday Nov 19, 10:30–11:00 AM Pacific**. Never substitute Monday Nov 16. Save date, weekday=3 and time=10:30 before clarification; reject inconsistent later tool arguments. Confirmation is still required. |
| K03 | Create one controlled 30-minute fixture, then request a second meeting at the same time, allowing overlap. | Show a second proposal with **“Allow overlapping meetings.”** After confirmation, both distinct events exist in Scheduling Demo at the same time. Listing keeps both. Cancel both test fixtures and verify the interval clears. |

Individual create/reschedule tools accept `allow_conflicts=true`. The user permits
double booking: an explicitly requested busy time can be prepared with the overlap
flag rather than silently moved to a different date/time. The proposal discloses
the flag and must be confirmed. Conflict-free scheduling remains available with
`allow_conflicts=false`. Available-slot suggestions still represent ordinary free
slots; an overlap proposal must not claim the time is free.

The adapter sends Cal.com's documented `allowConflicts` on the supported write
API version. It does not send `allowBookingOutOfBounds` or `skipBookingLimits`;
Cal.com still enforces its other rules and access permissions. The override can
be ignored by Cal.com for a caller without host/owner access, so only verified
read-back is reported as success. See the official
[creation contract](https://cal.com/docs/api-reference/v2/bookings/create-a-booking)
and [rescheduling contract](https://cal.com/docs/api-reference/v2/bookings/reschedule-a-booking).

K02 was checked with the configured local Qwen and a synthetic calendar, stopping
at the correct proposal; no invitation was sent to Matt. K03's live result and
cleanup evidence are summarized in the
[sanitized results](reports/live-acceptance-2026-11-13-summary.md). Raw JSON reports
are local-only and excluded from the submission. These follow-up
checks do not replace or rerun the historical 22-case acceptance report.

### K04 — Exact-time completion and non-blocking conflicts

This supersedes the earlier per-request overlap opt-in behavior for the chatbot.
The chatbot allows overlaps by default; its ordinary proposal/confirmation gate
still applies. A time conflict does not add another approval step or cause the
assistant to substitute another time.

| Step | Input / setup | Expected output |
| --- | --- | --- |
| 1 | “Set up a 1:1 with Matt next Thu at 10:30.” | Save the exact date/time and Matt; ask only for the email. |
| 2 | Supply the attendee email. | Immediately prepare the saved Thursday 10:30–11:00 meeting. Do not show 10:00–10:30 or ask the user to choose again. |
| 3 | Confirm with another controlled appointment already overlapping that time. | Create and verify the requested booking; keep both appointments. Then say: “This meeting has a time conflict with another event. It is booked at your requested time. You can ask me to suggest a different time.” |
| 4 | Do not request a move. | Both meetings remain at their original times. No automatic de-conflicting. |

The controller completes exact-time drafts after email-only replies without a
second model interpretation. If the model still asks for availability, the
controller prepares the saved exact time instead of rendering alternate slots.
Requests without an exact time can still ask the user to choose a free slot.

Conflict evidence is collected as a best-effort read and displayed only after a
verified write. Calendar read failures do not prevent booking; the reply states
when conflict status could not be checked. Rescheduling excludes the original
booking, and ambiguous provider busy intervals are not treated as proof of a
second event. The app allows conflicts by default; direct service callers can
still request strict free-slot validation with `allow_conflicts=False`.

# Live acceptance results — November 13, 2026

Latest results: **21 Pass, 0 Fail, 1 Blocked** across 22 cases.

Real configured `qwen3:30b-a3b`, real Cal.com, Google **Scheduling Demo**, timezone `America/Los_Angeles`; test appointments used Monday November 16, 2026. Invitations used the account-owner test mailbox. Core cases used the actual ChatSession/Conversation flow. Regression setup used real service-created fixtures; conversational assertions then used the actual model. R08 included Streamlit AppTest rerenders, rather than a manual browser check.

| Case | Latest result | Observed evidence |
| --- | --- | --- |
| C01 | Pass | Creation proposed; no write before confirmation. |
| C02 | Pass | Confirmed creation read back in Cal.com and Google. |
| C03 | Pass | Test meeting listed once with readable Pacific times. |
| C04 | Pass | Actual afternoon options; no unconfirmed move. |
| C05 | Pass | Chosen time prepared for confirmation. |
| C06 | Pass | Confirmed move and new Google start/end verified. |
| C07 | Pass | Listing reflected the moved test meeting. |
| C08 | Pass | Cancellation proposal; no early write. |
| C09 | Pass | Cal.com cancellation verified; previously busy Google interval cleared. |
| C10 | Pass | Cancelled test booking absent from the listing. |
| R01 | Pass | Next Monday resolved correctly and rendered in local time. |
| R02 | Pass | Saved numbered reference moved correctly; Google verified. |
| R03 | Pass | Seminar search/selection retained 4 PM; Google move verified. |
| R04 | Pass | Start unchanged, duration extended to 60 minutes; Google verified. |
| R05 | Pass | Unsupported duration rejected; original Google event unchanged. |
| R06 | Pass | Discard revoked the move; later confirmation caused no write. |
| R07 | Pass | Revised proposal replaced the original; only revised move applied. |
| R08 | Pass | Duplicate confirmation and Streamlit AppTest rerender caused no replay. |
| R09 | Pass | Distinct same-title bookings stayed separate; both Google events verified. |
| R10 | Blocked | Google-only writer not connected. Existing cancelled-Google/accepted-Cal.com mismatch was excluded correctly in the read-only check. |
| R11 | Pass | Listing → afternoon options → listing completed without a write or tool-limit failure. |
| R12 | Pass | Unavailable 3 AM move rejected; original Google event unchanged. |

## Fixes found by running the checklist

- Reschedule requests no longer send a multi-duration-only field to fixed-duration templates.
- Empty optional reasons are omitted; cancellation without a supplied reason previews and sends “User requested cancellation,” as required by the observed live account.
- Thinking-only Qwen metadata is respected, with enough output budget for a completed tool call. Hidden thinking is discarded.
- Static seminar title templates can prepare bookings without requiring the special 1:1 naming pattern.
- Exact-time availability checks can continue to a proposal, while broad afternoon requests offer choices.
- A unique saved booking/template match can supply a missing reference for read-only availability; returned timestamps are local.
- Confirmation without a pending proposal is a deterministic no-op.

## Verification limits and cleanup

Google creation/rescheduling checks read linked event title, start, end, status and destination calendar. Cancellation can remove Cal.com’s reference, so the runner separately confirms that an interval previously observed busy on Scheduling Demo becomes clear. This verifies absence of an active busy event in the interval; it does not inspect Google trash history. No missing-reference response alone is counted as proof of deletion.

R10 still needs a direct Google Calendar connection to create a new Google-only cancellation mismatch. Its existing-mismatch read-only check passed. No mock result substitutes for the blocked mutation.

Initial failures and intermediate retests are described in progress.md. Raw JSON evidence is retained locally and excluded from submission because it contains live calendar references. The table uses the most recent completed result for each ID, not a claim that every intermediate run passed. API rate limiting interrupted one retest; journalled cleanup completed after cooldown and the runner now paces requests.

Final cleanup: **0 active test bookings**, **0 remaining test templates**. Cleanup was checked through provider read-back. Existing appointments were not modified.

## Later regression checks

Subsequent fixes were checked separately with the configured model and synthetic
calendars for attendee/date/time carry-over, plus controlled live overlapping
bookings. Two distinct simultaneous Google events were verified, a detected
conflict was returned without blocking the second write, and both fixtures were
cancelled with their interval verified clear. The original 22-case table above
remains a historical result, not a claim that every case was rerun for each later
change. The latest offline suite contains 232 passing tests. Raw overlap reports
remain local and are not part of the submission.

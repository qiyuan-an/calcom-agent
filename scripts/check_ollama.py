"""Opt-in real Ollama check with synthetic calendar reads and fake writes only.

Run: .venv/bin/python -m scripts.check_ollama --model <installed-model>
Never loads .env or contacts Cal.com. Prints only scenario outcomes.
"""

import argparse
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from calcom_agent.client import Booking, EventType, Slot
from calcom_agent.conversation import Conversation
from calcom_agent.ollama import OllamaProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:30b-a3b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    now = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
    start = datetime(2026, 11, 13, 17, tzinfo=timezone.utc)
    client = Mock()
    client.list_event_types.return_value = [EventType(1, "Demo intro", 30, [], [])]
    client.get_event_type.return_value = client.list_event_types.return_value[0]
    client.list_bookings.return_value = [Booking("synthetic-uid", "Demo intro", "accepted", start, start + timedelta(minutes=30), 1)]
    client.available_slots.side_effect = lambda event_type_id, start, end, **kw: [Slot(start, start + timedelta(minutes=kw.get("duration") or 30))]
    executor = Mock()
    executor.execute.return_value = {"status": "accepted", "uid": "synthetic-uid"}
    provider = OllamaProvider(args.model, args.endpoint)
    chat = Conversation(provider, client, executor=executor, clock=lambda: now)
    try:
        chat.turn("List my Cal.com bookings tomorrow, November 13, 2026.")
        assert len(chat.candidates) == 1, "listing did not fetch candidates"
        print("PASS: listing")
        chat.turn("Move the first booking to tomorrow at 11am Pacific. Prepare it for confirmation.")
        assert chat.pending and chat.pending.operation == "reschedule", "reschedule proposal missing"
        chat.turn("confirm")
        assert executor.execute.call_args.args[0] == "reschedule"
        assert executor.execute.call_args.args[1]["start"] == "2026-11-13T19:00:00Z", "relative date was resolved incorrectly"
        print("PASS: reschedule follow-up and confirmation")
        chat.turn("Cancel the first booking. Prepare it for confirmation.")
        assert chat.pending and chat.pending.operation == "cancel", "cancel proposal missing"
        chat.turn("confirm")
        print("PASS: cancellation")
        chat.turn("Book a 30-minute Demo intro tomorrow November 13 at 9am Pacific for Demo Person, demo@example.test. Prepare it for confirmation.")
        assert chat.pending and chat.pending.operation == "create", "create proposal missing"
        chat.turn("Actually change that new booking to 10am tomorrow; keep the same attendee.")
        assert chat.pending and "10:00:00" in chat.pending.summary, "correction did not preserve creation"
        chat.turn("confirm")
        assert executor.execute.call_args.args[1]["start"] == "2026-11-13T18:00:00Z"
        print("PASS: creation, correction, and confirmation")
        assert executor.execute.call_count == 3
    finally:
        provider.close()


if __name__ == "__main__":
    main()

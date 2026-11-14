"""Opt-in live Cal.com lifecycle. Run locally; never run in automated tests.

Reads private .env at runtime, uses a user-designated test attendee, and requires
an explicit confirmation for every write. May send real invitations/updates.
No credentials, attendee details, titles, or booking UIDs are printed.
"""

from datetime import datetime, timedelta, timezone
from getpass import getpass

from calcom_agent.client import CalcomClient, CalcomError
from calcom_agent.config import load_settings, ConfigError
from calcom_agent.scheduling import SchedulingService, SchedulingError


def confirm(operation, start=None):
    print(f"Proposed demo action: {operation}" + (f" at {start.isoformat()}" if start else ""))
    return input("Type confirm to apply this one change (anything else stops): ").strip() == "confirm"


def main():
    print("LIVE test: Cal.com may send real invitations and calendar updates.")
    print("First verify your event type uses Scheduling Demo as its destination and conflict calendar.")
    if input("Type ready after verifying that setup: ").strip() != "ready":
        return
    try:
        settings = load_settings()
        event_id = int(input("Demo event type numeric ID: "))
        name = getpass("Designated test attendee name (hidden): ")
        email = getpass("Designated test attendee email (hidden): ")
        with CalcomClient(settings) as client:
            service = SchedulingService(client)
            event = client.get_event_type(event_id)
            start = datetime.now(timezone.utc) + timedelta(days=1)
            slots = client.available_slots(event_id, start, start + timedelta(days=7), duration=event.length_in_minutes)
            if len(slots) < 2:
                print("Need at least two available demo slots. No booking created.")
                return
            proposal = service.prepare_create(event_id, slots[0].start, name=name, email=email,
                                               time_zone=settings.default_timezone)
            if not confirm("create one demo booking", slots[0].start):
                return
            booking = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
            uid = booking["uid"]
            print("PASS: created; returned status:", booking["status"])
            print("Read-back verification:", booking["verification"])
            assert any(b.uid == uid for b in client.list_bookings()), "listing verification failed"
            print("PASS: listed created booking")
            print("Privately inspect Cal.com and Scheduling Demo before continuing.")
            proposal = service.prepare_reschedule(uid, slots[1].start, reason="Demo lifecycle verification")
            if confirm("reschedule this demo booking", slots[1].start):
                booking = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
                uid = booking["uid"]
                assert any(b.uid == uid and b.start == slots[1].start for b in client.list_bookings())
                print("PASS: rescheduled and listed returned booking UID")
                print("Read-back verification:", booking["verification"])
            else:
                print("Reschedule skipped; the created booking still needs cleanup.")
            print("Privately inspect the moved booking/calendar entry before cleanup.")
            proposal = service.prepare_cancel(uid, reason="Demo lifecycle cleanup")
            if confirm("cancel the demo booking for cleanup"):
                result = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
                assert result["status"] == "cancelled" and client.get_booking(uid)["status"] == "cancelled"
                print("PASS: cancellation and cleanup verified in Cal.com API")
                print("Read-back verification:", result["verification"])
            else:
                print("Cleanup skipped. Cancel the demo booking privately in Cal.com.")
    except (CalcomError, SchedulingError) as error:
        print("Stopped with sanitized error code:", error.code)
        print("Inspect Cal.com and clean up any demo booking manually before retrying; no write was retried.")
    except ConfigError as error:
        # ConfigError messages contain field names only, never their values.
        print(f"Configuration needed: {error}.")
        print("Run from the repository root and check the named setting in your private .env.")
        print("Existing environment variables override .env values. No API requests or booking changes were made.")
    except (ValueError, AssertionError):
        print("Setup or verification failed. Inspect Cal.com and clean up any created demo booking before retrying.")


if __name__ == "__main__":
    main()

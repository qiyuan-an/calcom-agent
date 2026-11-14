"""Deterministic action preparation and confirmation, independent of models.

One SchedulingService belongs to one trusted user's session. Confirmed actions
reach an injected executor; preparation never performs writes.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Callable, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from .client import Booking, CalcomClient, EventType, ONE_ON_ONE_NAME, parse_time, utc_string, unique_bookings


class SchedulingError(ValueError):
    """Stable error code plus field names; no attendee values in error text."""
    def __init__(self, code: str, fields: tuple[str, ...] = ()):
        self.code, self.fields = code, fields
        super().__init__(code + (": " + ", ".join(fields) if fields else ""))


def local_time(value: datetime, time_zone: str, *, fold: int | None = None) -> datetime:
    """Resolve a naive wall time, rejecting DST gaps and unchosen repeated times."""
    if value.tzinfo is not None:
        raise SchedulingError("expected_local_wall_time")
    zone = ZoneInfo(time_zone)
    candidates = {}
    for choice in (0, 1):
        aware = value.replace(tzinfo=zone, fold=choice)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == value:
            candidates[choice] = utc
    if not candidates:
        raise SchedulingError("nonexistent_local_time")
    if fold is not None and fold not in (0, 1):
        raise SchedulingError("invalid_fold")
    if len(set(candidates.values())) > 1 and fold is None:
        raise SchedulingError("ambiguous_local_time")
    return candidates[fold if fold is not None else next(iter(candidates))]


def resolve_booking(candidates: list[Booking], *, uid: str | None = None,
                    number: int | None = None, title: str | None = None) -> Booking:
    """Resolve only against a previously fetched candidate list; never guess."""
    if sum(x is not None for x in (uid, number, title)) != 1:
        raise SchedulingError("choose_one_booking_reference")
    if number is not None:
        if type(number) is not int or not 1 <= number <= len(candidates):
            raise SchedulingError("booking_not_found")
        return candidates[number - 1]
    matches = [b for b in candidates if b.uid == uid] if uid is not None else [
        b for b in candidates if b.title.casefold() == title.casefold()]
    if not matches:
        raise SchedulingError("booking_not_found")
    if len(matches) != 1:
        raise SchedulingError("ambiguous_booking")
    return matches[0]


class ActionExecutor(Protocol):
    def execute(self, operation: str, payload: dict) -> object: ...


@dataclass(frozen=True)
class Proposal:
    token: str
    operation: str
    summary: str
    expires_at: datetime


class SchedulingService:
    def __init__(self, client: CalcomClient, *, clock: Callable[[], datetime] | None = None,
                 confirmation_ttl: timedelta = timedelta(minutes=5)):
        if confirmation_ttl <= timedelta(0):
            raise ValueError("Confirmation TTL must be positive")
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ttl = confirmation_ttl
        self._pending = None

    def discard(self):
        self._pending = None

    @staticmethod
    def _standard(event: EventType) -> bool:
        p = event.policy
        return not (p.get("price", 0) or p.get("isInstantEvent") or p.get("requiresBookerEmailVerification")
                or (p.get("recurrence") and not p["recurrence"].get("disabled", False))
                or p.get("seatsPerTimeSlot")
                or (p.get("seats") and not p["seats"].get("disabled", False)))

    def _event(self, event_id: int) -> EventType:
        event = self._client.get_event_type(event_id)
        if not self._standard(event):
            raise SchedulingError("unsupported_event_type")
        return event

    def _future(self, start: datetime):
        utc_string(start)
        if start.astimezone(timezone.utc).astimezone(start.tzinfo).replace(tzinfo=None) != start.replace(tzinfo=None):
            raise SchedulingError("nonexistent_local_time")
        if start.astimezone(timezone.utc) <= self._clock().astimezone(timezone.utc):
            raise SchedulingError("time_must_be_in_future")

    def _slot(self, event: EventType, start: datetime, duration: int, uid=None, *, allow_conflicts=False):
        self._future(start)
        start = start.astimezone(timezone.utc)
        options = event.policy.get("lengthInMinutesOptions") or [event.length_in_minutes]
        if type(duration) is not int or duration <= 0 or duration not in options:
            raise SchedulingError("unsupported_duration")
        if type(allow_conflicts) is not bool:
            raise SchedulingError("invalid_allow_conflicts")
        if allow_conflicts:
            return
        end = start + timedelta(minutes=duration)
        slots = self._client.available_slots(event.id, start, end, duration=duration,
                                             booking_uid_to_reschedule=uid)
        if not any(s.start == start and s.end == end for s in slots):
            raise SchedulingError("slot_unavailable")

    def _booking(self, uid: str) -> Booking:
        booking = resolve_booking(unique_bookings(self._client.list_bookings()), uid=uid)
        if booking.status not in {"accepted", "pending"}:
            raise SchedulingError("booking_not_active")
        self._future(booking.start)
        return booking

    @staticmethod
    def _fingerprint(event, booking=None):
        return deepcopy((event, booking))

    def _save(self, operation, payload, event, booking, summary):
        proposal = Proposal(uuid4().hex, operation, summary, self._clock() + self._ttl)
        self._pending = (proposal, deepcopy(payload), self._fingerprint(event, booking))
        return proposal

    def _check_new_event_type(self, duration: int, slug: str):
        # An API failure must not be treated as an empty account. Refresh again
        # at confirmation to prevent creating a template that is no longer needed.
        for event in self._client.list_event_types():
            if event.slug == slug:
                raise SchedulingError("event_type_slug_exists")
            options = event.policy.get("lengthInMinutesOptions") or [event.length_in_minutes]
            if duration in options and self._standard(event):
                raise SchedulingError("reuse_existing_event_type", ("event_type_id",))

    def prepare_create_event_type(self, *, title: str, slug: str, duration: int) -> Proposal:
        self.discard()
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise SchedulingError("invalid_event_type_title")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 100:
            raise SchedulingError("invalid_event_type_slug")
        if type(duration) is not int or duration < 1:
            raise SchedulingError("invalid_duration")
        self._check_new_event_type(duration, slug)
        payload = {"title": title.strip(), "slug": slug, "lengthInMinutes": duration,
                   "locations": [{"type": "integration", "integration": "cal-video"}],
                   "customName": ONE_ON_ONE_NAME}
        return self._save("create_event_type", payload, None, None,
                          f"Create a reusable Cal.com meeting template '{title.strip()}' ({duration} minutes), "
                          f"link slug '{slug}', location Cal Video. Use your account's default availability "
                          "and calendar settings (verify Scheduling Demo is the destination). "
                          "This creates a template only; booking requires a separate confirmation.")

    def prepare_create(self, event_type_id: int, start: datetime, *, name: str = "",
                       email: str = "", time_zone: str = "", duration: int | None = None,
                       answers: dict | None = None, title: str = "", allow_conflicts: bool = False) -> Proposal:
        self.discard()  # An edit, even an invalid one, revokes the old confirmation.
        missing = tuple(k for k, v in (("name", name), ("email", email), ("timeZone", time_zone))
                        if not isinstance(v, str) or not v.strip())
        if missing:
            raise SchedulingError("missing_information", missing)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise SchedulingError("invalid_email", ("email",))
        ZoneInfo(time_zone)
        event = self._event(event_type_id)
        answers = deepcopy(answers or {})
        if not isinstance(answers, dict):
            raise SchedulingError("invalid_booking_answers")
        allowed = {f["slug"] for f in event.booking_fields if "slug" in f}
        if not isinstance(title, str) or len(title) > 200:
            raise SchedulingError("invalid_booking_answers", ("title",))
        if "title" in allowed:
            if event.policy.get("customName") == event.title:
                inferred = event.title
            else:
                host = self._client.get_profile()["name"].split()[0]
                inferred = f"{name.split()[0]}/{host} 1:1"
            title = answers.get("title") or title.strip() or inferred
            if not isinstance(title, str):
                raise SchedulingError("invalid_booking_answers", ("title",))
            if event.policy.get("customName") in {ONE_ON_ONE_NAME, event.title} and title != inferred:
                raise SchedulingError("unsupported_custom_title")
            answers["title"] = title
        if answers.get("title") and event.policy.get("customName") not in {ONE_ON_ONE_NAME, event.title}:
            raise SchedulingError("title_template_not_configured")
        if set(answers) - allowed:
            raise SchedulingError("unknown_booking_fields")
        builtin = {"name": name, "email": email, "timeZone": time_zone}
        missing = tuple(f["slug"] for f in event.booking_fields
                        if f.get("required") and (answers.get(f["slug"], builtin.get(f["slug"])) in (None, "", [])))
        if missing:
            raise SchedulingError("missing_information", missing)
        for field in event.booking_fields:
            value = answers.get(field.get("slug"))
            if value is None:
                continue
            if isinstance(value, str) and not value.strip() and field.get("required"):
                raise SchedulingError("missing_information", (field["slug"],))
            kind = field.get("type")
            if ((kind in {"text", "textarea", "email", "phone", "select"} and not isinstance(value, str))
                    or (kind == "checkbox" and type(value) is not bool)
                    or (kind == "number" and type(value) not in {int, float})):
                raise SchedulingError("invalid_booking_answers", (field["slug"],))
        # JSON-safe snapshot; reject arbitrary Python objects before confirmation.
        try:
            json.dumps(answers, allow_nan=False)
        except (TypeError, ValueError):
            raise SchedulingError("invalid_booking_answers") from None
        duration = event.length_in_minutes if duration is None else duration
        self._slot(event, start, duration, allow_conflicts=allow_conflicts)
        if len(event.locations) > 1:
            raise SchedulingError("location_choice_required")
        if event.locations and event.locations[0].get("type") in {"attendeeAddress", "attendeePhone", "attendeeDefined"}:
            raise SchedulingError("unsupported_location")
        payload = {"eventTypeId": event.id, "start": utc_string(start),
                   "attendee": builtin, "bookingFieldsResponses": answers}
        if event.policy.get("lengthInMinutesOptions"):
            payload["lengthInMinutes"] = duration
        if allow_conflicts:
            payload["allowConflicts"] = True
        return self._save("create", payload, event, None,
                          f"Create {answers.get('title', title or event.title)}, {duration} minutes, {start.astimezone(ZoneInfo(time_zone)).isoformat()}, with {name} ({email})" + (". Allow overlapping meetings." if allow_conflicts else ""))

    def prepare_cancel(self, uid: str, *, reason: str = "") -> Proposal:
        self.discard()
        if not isinstance(reason, str):
            raise SchedulingError("invalid_reason")
        reason = reason.strip() or "User requested cancellation"
        booking = self._booking(uid)
        event = self._event(booking.event_type_id)
        if event.policy.get("disableCancelling", {}).get("disabled"):
            raise SchedulingError("cancellation_disabled")
        return self._save("cancel", {"bookingUid": uid, "cancellationReason": reason}, event, booking,
                          f"Cancel {booking.title} at {utc_string(booking.start)}; reason: {reason}")

    def prepare_update(self, uid: str, *, location: str | None = None, duration: int | None = None) -> Proposal:
        self.discard()
        if duration is not None:
            if location is not None:
                raise SchedulingError("choose_one_update_field", ("location", "duration"))
            return self.prepare_reschedule(uid, duration=duration)
        if location is None:
            raise SchedulingError("missing_information", ("location", "duration"))
        if not isinstance(location,str) or not location.strip() or len(location)>500:
            raise SchedulingError('invalid_booking_answers', ('location',))
        booking = self._booking(uid)
        event = self._event(booking.event_type_id)
        return self._save('update', {'bookingUid':uid,'location':location}, event, booking,
                          f'Update {booking.title} at {utc_string(booking.start)}: location {location}')

    def prepare_reschedule(self, uid: str, start: datetime | None = None, *, reason: str = "",
                           duration: int | None = None, allow_conflicts: bool = False) -> Proposal:
        self.discard()
        if not isinstance(reason, str):
            raise SchedulingError("invalid_reason")
        booking = self._booking(uid)
        event = self._event(booking.event_type_id)
        restriction = event.policy.get("disableRescheduling", {})
        if restriction.get("disabled"):
            raise SchedulingError("rescheduling_disabled")
        if start is None and duration is None:
            raise SchedulingError("missing_information", ("start", "duration"))
        start = booking.start if start is None else start
        requested_duration = duration
        duration = int((booking.end - booking.start).total_seconds() / 60) if duration is None else duration
        self._slot(event, start, duration, uid, allow_conflicts=allow_conflicts)
        payload = {"bookingUid": uid, "start": utc_string(start), "reschedulingReason": reason}
        if requested_duration is not None and event.policy.get("lengthInMinutesOptions"):
            payload["lengthInMinutes"] = duration
        if allow_conflicts:
            payload["allowConflicts"] = True
        summary = (f"Move {booking.title} from {utc_string(booking.start)} to {utc_string(start)}; reason: {reason}"
                   if requested_duration is None else
                   f"Update {booking.title}: {utc_string(booking.start)} to {utc_string(start)}, "
                   f"duration {duration} minutes, ending {utc_string(start + timedelta(minutes=duration))}; reason: {reason}")
        if allow_conflicts:
            summary += ". Allow overlapping meetings."
        return self._save("reschedule", payload, event, booking, summary)

    def execute_confirmed(self, token: str, *, confirmed: bool, executor: ActionExecutor):
        if confirmed is not True:
            raise SchedulingError("confirmation_required")
        pending = self._pending
        if pending is None or pending[0].token != token:
            raise SchedulingError("invalid_confirmation")
        # Consume before reads or execution: failures require a newly prepared action.
        self.discard()
        proposal, payload, snapshot = pending
        if self._clock() >= proposal.expires_at:
            raise SchedulingError("confirmation_expired")
        if proposal.operation == "create_event_type":
            self._check_new_event_type(payload["lengthInMinutes"], payload["slug"])
            return executor.execute(proposal.operation, deepcopy(payload))
        if proposal.operation == "create_series":
            from .series import execute
            return execute(self._client, payload, executor=executor)
        old_event, old_booking = snapshot
        booking = self._booking(old_booking.uid) if old_booking else None
        event = self._event(old_event.id)
        if self._fingerprint(event, booking) != snapshot:
            raise SchedulingError("stale_confirmation")
        if proposal.operation in {"create", "reschedule"}:
            start = parse_time(payload["start"])
            duration = payload.get("lengthInMinutes", int((booking.end - booking.start).total_seconds() / 60) if booking
                                   else event.length_in_minutes)
            self._slot(event, start, duration, booking.uid if booking else None,
                       allow_conflicts=payload.get("allowConflicts", False))
        return executor.execute(proposal.operation, deepcopy(payload))

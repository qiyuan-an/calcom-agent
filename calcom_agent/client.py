"""Cal.com v2 client. No scheduling policy or model dependencies.

Endpoint contracts: https://cal.com/docs/api-reference/v2/
Versions are intentionally endpoint-specific. No raw provider errors are exposed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
import re
from zoneinfo import ZoneInfo

import httpx

from .config import Settings

ONE_ON_ONE_NAME = "{Scheduler first name}/{Organiser first name} 1:1"


class CalcomError(Exception):
    def __init__(self, code: str, status_code: int | None = None, *, resource: str | None = None, details: dict | None = None):
        self.code = code
        self.details = details or {}
        self.resource = resource
        self.status_code = status_code
        super().__init__(f"Cal.com request failed: {code}")


def utc_string(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    utc_string(result)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class EventType:
    id: int
    title: str
    length_in_minutes: int
    booking_fields: list[dict[str, Any]]
    locations: list[dict[str, Any]]
    policy: dict[str, Any] = field(default_factory=dict)
    slug: str = ""


@dataclass(frozen=True)
class Booking:
    uid: str
    title: str
    status: str
    start: datetime
    end: datetime
    event_type_id: int | None


def unique_bookings(bookings: list[Booking]) -> list[Booking]:
    """Collapse repeated API rows by UID; conflicting snapshots need a refresh."""
    by_uid = {}
    for booking in bookings:
        previous = by_uid.get(booking.uid)
        if previous is not None and previous != booking:
            raise CalcomError("invalid_response", resource="bookings")
        by_uid[booking.uid] = booking
    return list(by_uid.values())


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


class CalcomClient:
    BASE_URL = "https://api.cal.com/v2/"
    ME_VERSION = "2024-06-11"
    EVENT_VERSION = "2026-06-12"
    SLOT_VERSION = "2024-09-04"
    BOOKING_VERSION = "2026-05-01"
    WRITE_VERSION = "2026-02-25"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self._username = settings.cal_username
        # Fixed Cal.com origin; never follow redirects carrying credentials.
        self._http = httpx.Client(
            base_url=self.BASE_URL, timeout=20, follow_redirects=False,
            trust_env=False, transport=transport,
            headers={"Authorization": f"Bearer {settings.cal_api_key}", "Accept": "application/json"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._http.close()

    @staticmethod
    def _uid_path(uid: str) -> str:
        if not isinstance(uid, str) or not uid or len(uid) > 256 or any(c in uid for c in "/?#%\\") or uid in {".", ".."}:
            raise ValueError("Invalid booking identifier")
        return "bookings/" + quote(uid, safe="")

    @staticmethod
    def _booking_result(data) -> dict:
        try:
            if not isinstance(data, dict) or not all(isinstance(data[k], str) and data[k] for k in ("uid", "status", "title")):
                raise ValueError()
            start, end = parse_time(data["start"]), parse_time(data["end"])
            CalcomClient._positive(data["eventTypeId"])
            if start >= end:
                raise ValueError()
            result = {k: data[k] for k in ("uid", "status", "title", "eventTypeId")}
            result.update(start=utc_string(start), end=utc_string(end))
        except (KeyError, TypeError, ValueError):
            result = None
        if result is None:
            raise CalcomError("invalid_response")
        return result

    def get_booking(self, uid: str) -> dict:
        return self._booking_result(self._get(self._uid_path(uid), self.WRITE_VERSION)["data"])

    def get_profile(self) -> dict:
        data = self._get("me", self.ME_VERSION)["data"]
        if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
            raise CalcomError("invalid_response")
        return {"name": data["name"], "username": data.get("username", "")}

    def verify_google_calendar(self, booking: dict, *, cancelled=False, references=None) -> dict:
        """Read provider event back; a booking reference alone is not proof of sync."""
        try:
            refs = references if references is not None else self._google_references(booking["uid"])
            if not isinstance(refs, list):
                raise CalcomError("invalid_response")
            google = [r for r in refs if isinstance(r, dict) and r.get("type") == "google_calendar" and r.get("eventUid")]
            if not google:
                return {"status": "unverified", "reason": "no_google_reference"}
            for ref in google:
                event_id = ref["eventUid"]
                # Reuse the identifier validation before constructing a provider path.
                self._uid_path(event_id)
                try:
                    event = self._get("calendars/google/event/" + quote(event_id, safe=""), self.ME_VERSION)["data"]
                except CalcomError as error:
                    if cancelled and error.code == "not_found":
                        # This endpoint resolves credentials via Cal.com's booking
                        # reference. Cancellation can delete that reference, so a
                        # 404 alone cannot prove deletion from Google.
                        return {"status": "unverified", "reason": "google_reference_removed"}
                    raise
                if not isinstance(event, dict):
                    raise CalcomError("invalid_response")
                if cancelled:
                    if event.get("status") not in {"cancelled", "canceled"}:
                        return {"status": "unverified", "reason": "google_event_still_active"}
                elif event.get("status") in {"cancelled", "canceled"}:
                    return {"status": "unverified", "reason": "google_event_cancelled" if len(google) == 1 else "google_event_mismatch"}
                elif (event.get("title") != booking["title"]
                      or parse_time(event["start"]["time"]) != parse_time(booking["start"])
                      or parse_time(event["end"]["time"]) != parse_time(booking["end"])):
                    return {"status": "unverified", "reason": "google_event_mismatch"}
            return {"status": "verified"}
        except (CalcomError, KeyError, TypeError, ValueError) as error:
            return {"status": "unverified", "reason": getattr(error, "code", "invalid_response")}

    def _google_references(self, uid):
        return self._get(self._uid_path(uid) + "/references", self.WRITE_VERSION,
                         {"type": "google_calendar"})["data"]

    def _verify_booking_write(self, operation: str, payload: dict, result: dict, *, previous=None, references=None) -> dict:
        failure = False
        try:
            data = self._get(self._uid_path(result["uid"]), self.WRITE_VERSION)["data"]
            observed = self._booking_result(data)
            if observed != result:
                raise CalcomError("verification_failed")
            if operation == "cancel":
                if observed["status"] != "cancelled" or observed["uid"] != payload["bookingUid"]:
                    raise CalcomError("verification_failed")
            else:
                if observed["status"] not in {"accepted", "pending"} or parse_time(observed["start"]) != parse_time(payload["start"]):
                    raise CalcomError("verification_failed")
                if operation == "create":
                    if observed["eventTypeId"] != payload["eventTypeId"]:
                        raise CalcomError("verification_failed")
                    attendees = data.get("attendees", [])
                    if not any(a.get("email", "").casefold() == payload["attendee"]["email"].casefold()
                               and a.get("name", "").strip() == payload["attendee"]["name"].strip()
                               for a in attendees if isinstance(a, dict)):
                        raise CalcomError("verification_failed")
                    title = payload.get("bookingFieldsResponses", {}).get("title")
                    if title and observed["title"] != title:
                        raise CalcomError("verification_failed")
                    duration = payload.get("lengthInMinutes") or self.get_event_type(payload["eventTypeId"]).length_in_minutes
                else:
                    if observed["eventTypeId"] != previous["eventTypeId"] or observed["title"] != previous["title"]:
                        raise CalcomError("verification_failed")
                    duration = payload.get("lengthInMinutes", (parse_time(previous["end"]) - parse_time(previous["start"])).total_seconds() / 60)
                if parse_time(observed["end"]) != parse_time(payload["start"]) + timedelta(minutes=duration):
                    raise CalcomError("verification_failed")
        except (CalcomError, KeyError, TypeError, ValueError):
            failure = True
        if failure:
            error = CalcomError("verification_failed")
            error.booking_uid = result["uid"]
            raise error
        result["verification"] = {"calcom": "verified", "google_calendar": self.verify_google_calendar(
            observed, cancelled=operation == "cancel", references=references)}
        return result

    def _update_location(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'bookingUid','location'}:
            raise ValueError('Invalid location update payload')
        address = payload['location']
        if not isinstance(address,str) or not address.strip() or len(address)>500:
            raise ValueError('Invalid address')
        path = self._uid_path(payload['bookingUid'])
        before = self.get_booking(payload['bookingUid'])
        failure = None
        try:
            self._request('PATCH', path+'/location', '2024-08-13',
                          body={'location':{'type':'address','address':address}})
        except CalcomError as error:
            unknown = error.code in {'timeout','network','server_error','invalid_response'}
            failure = CalcomError('write_outcome_unknown' if unknown else error.code, error.status_code,
                                   resource=error.resource, details=error.details)
        if failure: raise failure
        try:
            raw = self._get(path,self.WRITE_VERSION)['data']
            observed = self._booking_result(raw)
            if observed != before or raw.get('location') != address:
                raise CalcomError('verification_failed')
        except CalcomError:
            failure = CalcomError('verification_failed')
            failure.booking_uid = payload['bookingUid']
        if failure: raise failure
        return {**observed,'location':address,'verification':{'calcom':'verified',
                'google_calendar':{'status':'unverified','reason':'location_not_checked'}}}

    def _conflicts_before_write(self, payload, previous=None):
        """Best-effort evidence for a post-write reminder; never blocks a write."""
        try:
            start = parse_time(payload["start"])
            minutes = payload.get("lengthInMinutes")
            if minutes is None:
                minutes = ((parse_time(previous["end"])-parse_time(previous["start"])).total_seconds()/60
                           if previous else self.get_event_type(payload["eventTypeId"]).length_in_minutes)
            end = start + timedelta(minutes=minutes)
            for booking in self.list_bookings():
                if (booking.uid == payload.get("bookingUid") or booking.status not in {"accepted", "pending"}
                        or booking.start >= end or booking.end <= start):
                    continue
                check = self.verify_google_calendar({"uid": booking.uid, "title": booking.title,
                    "start": utc_string(booking.start), "end": utc_string(booking.end)})
                if check.get("reason") != "google_event_cancelled":
                    return {"status": "detected", "source": "calcom"}
            calendars = self._get("calendars", self.ME_VERSION)["data"]
            selected = {(cal["credentialId"], cal["externalId"])
                        for connection in calendars["connectedCalendars"] for cal in connection.get("calendars", [])
                        if cal.get("isSelected")}
            destination = calendars.get("destinationCalendar")
            if destination:
                selected.add((destination["credentialId"], destination["externalId"]))
            if not selected:
                return {"status": "unknown"}
            params = [("dateFrom", start.date().isoformat()), ("dateTo", (end.date()+timedelta(days=1)).isoformat()),
                      ("timeZone", "UTC")]
            params += [("calendar", f"{credential},{external}") for credential, external in selected]
            busy = self._get("calendars/busy-times", self.ME_VERSION, params)["data"]
            overlaps = [row for row in busy if parse_time(row["start"]) < end and parse_time(row["end"]) > start]
            if previous and overlaps:
                # Busy intervals have no event identity. Do not call the original
                # booking a conflict, or claim other events hidden within it absent.
                old_start, old_end = parse_time(previous["start"]), parse_time(previous["end"])
                if all(parse_time(r["start"]) < old_end and parse_time(r["end"]) > old_start for r in overlaps):
                    return {"status": "unknown"}
            return {"status": "detected" if overlaps else "clear", "source": "calendar"}
        except (CalcomError, KeyError, TypeError, ValueError):
            return {"status": "unknown"}

    def execute(self, operation: str, payload: dict) -> dict:
        """Write adapter for SchedulingService; application callers must use its gate.

        Never retry POST. An unknown outcome requires read-only inspection before
        a new proposal. Only standard-booking payloads are supported.
        """
        if operation == 'update':
            return self._update_location(payload)
        if operation == "create_event_type":
            required_fields = {"title", "slug", "lengthInMinutes", "locations"}
            if not isinstance(payload, dict) or not required_fields <= set(payload) or set(payload) - required_fields - {"customName"}:
                raise ValueError("Invalid event type payload")
            if "customName" in payload and payload["customName"] not in {ONE_ON_ONE_NAME, payload["title"]}:
                raise ValueError("Invalid event naming pattern")
            self._positive(payload["lengthInMinutes"])
            if (not isinstance(payload["title"], str) or not payload["title"].strip()
                    or not isinstance(payload["slug"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", payload["slug"])
                    or payload["locations"] != [{"type": "integration", "integration": "cal-video"}]):
                raise ValueError("Invalid event type payload")
            failure = None
            try:
                event = self._events(self._request("POST", "event-types", self.EVENT_VERSION, body=payload)["data"], many=False)
            except CalcomError as error:
                unknown = error.code in {"timeout", "network", "server_error", "invalid_response"}
                failure = CalcomError("write_outcome_unknown" if unknown else error.code, error.status_code, resource=error.resource, details=error.details)
            if failure:
                raise failure
            from dataclasses import asdict
            try:
                observed = self.get_event_type(event.id)
                if (observed.title, observed.slug, observed.length_in_minutes) != (payload["title"], payload["slug"], payload["lengthInMinutes"]):
                    raise CalcomError("verification_failed")
                if "customName" in payload and observed.policy.get("customName") != payload["customName"]:
                    raise CalcomError("verification_failed")
            except CalcomError:
                failure = CalcomError("verification_failed")
            if failure:
                raise failure
            return {"eventType": asdict(event)}
        allowed = {
            "create": {"eventTypeId", "start", "attendee", "bookingFieldsResponses", "lengthInMinutes", "allowConflicts"},
            "cancel": {"bookingUid", "cancellationReason"},
            "reschedule": {"bookingUid", "start", "reschedulingReason", "lengthInMinutes", "allowConflicts"},
        }
        required = {"create": {"eventTypeId", "start", "attendee"},
                    "cancel": {"bookingUid"}, "reschedule": {"bookingUid", "start"}}
        if operation not in allowed or not isinstance(payload, dict) or set(payload) - allowed[operation] or required[operation] - set(payload):
            raise ValueError("Invalid mutation payload")
        if "allowConflicts" in payload and type(payload["allowConflicts"]) is not bool:
            raise ValueError("Invalid conflict override")
        if "lengthInMinutes" in payload:
            self._positive(payload["lengthInMinutes"])
        body = dict(payload)
        # Optional reason fields reject empty strings on the live API. Omit
        # absent reasons instead of sending a value that fails validation.
        for key in ("cancellationReason", "reschedulingReason"):
            if key in body and isinstance(body[key], str) and not body[key].strip():
                body.pop(key)
        previous, references = None, None
        if operation in {"reschedule", "cancel"}:
            previous = self.get_booking(payload["bookingUid"])
        if operation == "cancel":
            try:
                references = self._google_references(payload["bookingUid"])
            except CalcomError:
                references = []
        conflicts = (self._conflicts_before_write(payload, previous)
                     if operation in {"create", "reschedule"} and payload.get("allowConflicts") else None)
        path = "bookings" if operation == "create" else self._uid_path(body.pop("bookingUid")) + "/" + operation
        failure = None
        try:
            result = self._booking_result(self._request("POST", path, self.WRITE_VERSION, body=body)["data"])
        except CalcomError as error:
            unknown = error.code in {"timeout", "network", "server_error", "invalid_response"}
            failure = CalcomError("write_outcome_unknown" if unknown else error.code, error.status_code, resource=error.resource, details=error.details)
        if failure:
            raise failure
        result = self._verify_booking_write(operation, payload, result, previous=previous, references=references)
        if conflicts is not None:
            result["conflicts"] = conflicts
        return result

    def _get(self, path: str, version: str, params=None) -> dict:
        return self._request("GET", path, version, params=params)

    def _request(self, method: str, path: str, version: str, *, params=None, body=None) -> dict:
        # Raise outside exception handlers so raw request/response exceptions are
        # not retained as exception context (they may contain private data).
        error = None
        try:
            response = self._http.request(method, path, params=params, json=body, headers={"cal-api-version": version})
        except httpx.TimeoutException:
            error = CalcomError("timeout")
        except httpx.HTTPError:
            error = CalcomError("network")
        if error:
            raise error
        if not 200 <= response.status_code < 300:
            code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(
                response.status_code, "server_error" if response.status_code >= 500 else "http_error")
            resource = ("event_types" if path == "event-types" else
                        "event_type" if path.startswith("event-types/") else
                        "availability" if path == "slots" else "bookings")
            details = {'method':method, 'resource':resource, 'api_version':version}
            if response.status_code == 400:
                # Classify fixed categories without exposing raw provider text.
                try: message = str(response.json().get("error", {}).get("message", "")).casefold()
                except (ValueError, AttributeError): message = ""
                reasons = []
                if 'email' in message:
                    if 'cannot receive mail' in message:
                        reasons.append('email_undeliverable')
                    elif any(term in message for term in ('mx', 'deliverab', 'domain', 'dns')):
                        reasons.append('email_domain_deliverability')
                    elif any(term in message for term in ('format', 'syntax')):
                        reasons.append('email_format')
                    else: reasons.append('email_validation')
                if 'required' in message or 'missing' in message: reasons.append('required_fields')
                if 'availab' in message or 'slot' in message: reasons.append('availability')
                if 'recurr' in message: reasons.append('recurrence')
                details['validation_reasons'] = reasons
                if "email" in message and path == "bookings": code = "attendee_email_rejected"
                elif "availab" in message or "slot" in message: code = "slot_unavailable"
                elif "recurr" in message: code = "recurrence_rejected"
                else: code = "booking_validation_rejected" if path == "bookings" else "http_error"
            raise CalcomError(code, response.status_code, resource=resource, details=details)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or payload.get("status") != "success" or "data" not in payload:
            raise CalcomError("invalid_response")
        return payload

    def list_event_types(self) -> list[EventType]:
        payload = self._get("event-types", self.EVENT_VERSION, {"username": self._username})
        return self._events(payload["data"], many=True)

    def get_event_type(self, event_type_id: int) -> EventType:
        self._positive(event_type_id)
        failure = None
        try:
            payload = self._get(f"event-types/{event_type_id}", self.EVENT_VERSION)
        except CalcomError as error:
            failure = error
        if failure:
            # Public discovery can succeed for another user's profile while
            # authenticated detail reads fail. Diagnose this before blaming the template.
            if failure.code == "not_found":
                try:
                    owner = self._get("me", self.ME_VERSION)["data"]
                    username = owner.get("username") if isinstance(owner, dict) else None
                    if isinstance(username, str) and username.casefold() != self._username.casefold():
                        failure = CalcomError("account_mismatch", failure.status_code, resource="event_type")
                except CalcomError:
                    pass  # An inconclusive diagnostic must not replace the original failure.
            raise failure
        return self._events(payload["data"], many=False)

    @staticmethod
    def _events(data, *, many):
        try:
            rows = data if many else [data]
            if not isinstance(rows, list):
                raise ValueError()
            result = []
            for row in rows:
                if (type(row["id"]) is not int or type(row["lengthInMinutes"]) is not int
                        or row["id"] < 1 or row["lengthInMinutes"] < 1
                        or not isinstance(row["title"], str) or not isinstance(row.get("slug", ""), str)):
                    raise ValueError()
                fields, locations = row.get("bookingFields", []), row.get("locations", [])
                if not all(isinstance(items, list) and all(isinstance(x, dict) for x in items)
                           for items in (fields, locations)):
                    raise ValueError()
                policy_keys = ("lengthInMinutesOptions", "recurrence", "seats", "seatsPerTimeSlot",
                               "price", "isInstantEvent", "requiresBookerEmailVerification",
                               "disableCancelling", "disableRescheduling", "customName")
                policy = {key: row[key] for key in policy_keys if key in row}
                result.append(EventType(row["id"], row["title"], row["lengthInMinutes"], fields, locations, policy, row.get("slug", "")))
        except (KeyError, TypeError, ValueError):
            result = None
        if result is None:
            raise CalcomError("invalid_response")
        return result if many else result[0]

    @staticmethod
    def _positive(value):
        if type(value) is not int or value < 1:
            raise ValueError("Expected a positive integer")

    def available_slots(self, event_type_id: int, start: datetime, end: datetime, *,
                        time_zone: str = "UTC", duration: int | None = None,
                        booking_uid_to_reschedule: str | None = None) -> list[Slot]:
        self._positive(event_type_id)
        params = {"eventTypeId": event_type_id, "start": utc_string(start), "end": utc_string(end),
                  "timeZone": time_zone, "format": "range"}
        if start >= end:
            raise ValueError("Start must precede end")
        ZoneInfo(time_zone)
        if duration is not None:
            self._positive(duration)
            params["duration"] = duration
        if booking_uid_to_reschedule is not None:
            params["bookingUidToReschedule"] = booking_uid_to_reschedule
        data = self._get("slots", self.SLOT_VERSION, params)["data"]
        try:
            if not isinstance(data, dict) or not all(isinstance(rows, list) for rows in data.values()):
                raise ValueError()
            slots = [Slot(parse_time(row["start"]), parse_time(row["end"]))
                     for rows in data.values() for row in rows]
            if any(slot.start >= slot.end for slot in slots):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            slots = None
        if slots is None:
            raise CalcomError("invalid_response")
        return sorted(slots, key=lambda slot: slot.start)

    def list_bookings(self, *, status: str | None = None, after_start: datetime | None = None,
                      before_end: datetime | None = None, limit: int = 50,
                      max_pages: int = 100) -> list[Booking]:
        self._positive(limit)
        self._positive(max_pages)
        if limit > 100:
            raise ValueError("Limit must be at most 100")
        if status is not None and status not in {"upcoming", "recurring", "past", "cancelled", "unconfirmed"}:
            raise ValueError("Invalid booking status")
        params = {"limit": limit}
        if status is not None:
            params["status"] = status
        if after_start is not None:
            params["afterStart"] = utc_string(after_start)
        if before_end is not None:
            params["beforeEnd"] = utc_string(before_end)
        if after_start is not None and before_end is not None and after_start >= before_end:
            raise ValueError("Start must precede end")
        bookings, seen_cursors = [], set()
        for _ in range(max_pages):
            payload = self._get("bookings", self.BOOKING_VERSION, params)
            invalid = False
            try:
                if not isinstance(payload["data"], list):
                    raise ValueError()
                for row in payload["data"]:
                    if not all(isinstance(row[k], str) and row[k] for k in ("uid", "title", "status")):
                        raise ValueError()
                    # Deleted event types leave cancelled history with a null ID.
                    # Preserve that history; never permit a null ID on active bookings.
                    if row["eventTypeId"] is not None or row["status"] != "cancelled":
                        self._positive(row["eventTypeId"])
                    booking = Booking(row["uid"], row["title"], row["status"], parse_time(row["start"]),
                                      parse_time(row["end"]), row["eventTypeId"])
                    if booking.start >= booking.end:
                        raise ValueError()
                    bookings.append(booking)
                pagination = payload["pagination"]
                has_more = pagination["hasMore"]
                if type(has_more) is not bool:
                    raise ValueError()
                cursor = pagination.get("nextCursor")
                if has_more and (not isinstance(cursor, str) or not cursor):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                invalid = True
            if invalid:
                raise CalcomError("invalid_response")
            if not has_more:
                return sorted(unique_bookings(bookings), key=lambda booking: (booking.start, booking.uid))
            if cursor in seen_cursors:
                raise CalcomError("pagination_loop")
            seen_cursors.add(cursor)
            params["cursor"] = cursor
        raise CalcomError("pagination_limit")

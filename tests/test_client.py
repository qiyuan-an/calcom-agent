from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from calcom_agent.client import CalcomClient, CalcomError
from calcom_agent.config import Settings


START = datetime(2026, 11, 13, 9, tzinfo=ZoneInfo("America/Los_Angeles"))
END = datetime(2026, 11, 13, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
EVENT = {"id": 1, "title": "Demo", "lengthInMinutes": 30,
         "bookingFields": [{"slug": "company", "required": True}], "locations": [{"type": "link"}]}
BOOKING = {"uid": "synthetic-uid", "title": "Demo", "status": "accepted", "eventTypeId": 1,
           "start": "2026-11-13T17:00:00Z", "end": "2026-11-13T17:30:00Z"}


def client(handler):
    return CalcomClient(Settings("synthetic-key", "demo"), transport=httpx.MockTransport(handler))


def success(data, **extra):
    return httpx.Response(200, json={"status": "success", "data": data, **extra})


def test_event_list_and_detail_contracts():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.cal.com"
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.headers["cal-api-version"] == "2026-06-12"
        return success([EVENT] if request.url.path == "/v2/event-types" else EVENT)
    with client(handler) as api:
        event = api.list_event_types()[0]
        assert event.id == 1 and event.length_in_minutes == 30
        assert event.booking_fields == EVENT["bookingFields"]
        assert event.locations == EVENT["locations"]
        assert api.get_event_type(1) == event
    assert dict(requests[0].url.params) == {"username": "demo"}
    assert requests[1].url.path == "/v2/event-types/1"


def test_slots_send_utc_and_normalize_ranges_and_empty_results():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == "/v2/slots"
        assert request.headers["cal-api-version"] == "2024-09-04"
        return success({"2026-11-13": [{"start": "2026-11-13T09:00:00-08:00",
                                                "end": "2026-11-13T09:30:00-08:00"}]})
    with client(handler) as api:
        slots = api.available_slots(1, START, END, time_zone="America/Los_Angeles", duration=30,
                                    booking_uid_to_reschedule="synthetic-uid")
    assert dict(requests[0].url.params) == {
        "eventTypeId": "1", "start": "2026-11-13T17:00:00Z", "end": "2026-11-13T18:00:00Z",
        "timeZone": "America/Los_Angeles", "format": "range", "duration": "30",
        "bookingUidToReschedule": "synthetic-uid"}
    assert slots[0].start == START and slots[0].start.tzinfo == timezone.utc
    with client(lambda r: success({})) as api:
        assert api.available_slots(1, START, END) == []


def test_booking_cursor_pagination_filters_and_sorting():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["cal-api-version"] == "2026-05-01"
        assert request.url.path == "/v2/bookings"
        if len(requests) == 1:
            return success([BOOKING], pagination={"hasMore": True, "nextCursor": "opaque+cursor="})
        earlier = {**BOOKING, "uid": "earlier", "start": "2026-11-13T16:00:00Z"}
        return success([earlier], pagination={"hasMore": False, "nextCursor": None})
    with client(handler) as api:
        bookings = api.list_bookings(status="upcoming", after_start=START, before_end=END, limit=1)
    assert [b.uid for b in bookings] == ["earlier", "synthetic-uid"]
    assert dict(requests[0].url.params) == {
        "status": "upcoming", "afterStart": "2026-11-13T17:00:00Z",
        "beforeEnd": "2026-11-13T18:00:00Z", "limit": "1"}
    assert requests[1].url.params["cursor"] == "opaque+cursor="
    with client(lambda r: success([], pagination={"hasMore": False})) as api:
        assert api.list_bookings() == []


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"),
    (404, "not_found"), (429, "rate_limited"), (503, "server_error"), (400, "http_error"), (302, "http_error")])
def test_http_errors_are_sanitized_and_redirects_not_followed(status, code, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="synthetic-secret private-calendar-data",
                              headers={"Location": "https://other.example"})
    with client(handler) as api, pytest.raises(CalcomError) as exc:
        api.list_event_types()
    assert exc.value.code == code and exc.value.status_code == status
    assert "synthetic" not in str(exc.value) and "private-calendar" not in str(exc.value)
    assert "synthetic-key" not in caplog.text
    assert len(requests) == 1


@pytest.mark.parametrize("failure,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")])
def test_transport_failures_drop_raw_exception_context(failure, code):
    def handler(request):
        raise failure("synthetic-secret private-calendar-data", request=request)
    with client(handler) as api, pytest.raises(CalcomError) as exc:
        api.list_event_types()
    assert exc.value.code == code
    assert exc.value.__context__ is None


@pytest.mark.parametrize("response", [httpx.Response(200, text="not json"),
    httpx.Response(200, json={"status": "error", "error": "private-data"}),
    success({}), success([{"id": 1}])])
def test_invalid_event_responses_fail_closed(response):
    with client(lambda r: response) as api, pytest.raises(CalcomError, match="invalid_response"):
        api.list_event_types()


@pytest.mark.parametrize("pagination,code", [
    ({"hasMore": True}, "invalid_response"),
    ({"hasMore": "false"}, "invalid_response"),
    ({"hasMore": True, "nextCursor": "same"}, "pagination_loop"),
])
def test_bad_pagination_never_returns_partial_results(pagination, code):
    with client(lambda r: success([BOOKING], pagination=pagination)) as api:
        with pytest.raises(CalcomError, match=code):
            api.list_bookings()


def test_page_bound_and_malformed_booking_or_slot():
    with client(lambda r: success([BOOKING], pagination={"hasMore": True, "nextCursor": "next"})) as api:
        with pytest.raises(CalcomError, match="pagination_limit"):
            api.list_bookings(max_pages=1)
    with client(lambda r: success([{**BOOKING, "start": "invalid"}], pagination={"hasMore": False})) as api:
        with pytest.raises(CalcomError, match="invalid_response"):
            api.list_bookings()
    with client(lambda r: success({"day": [{"start": "2026-11-13T17:00:00"}]})) as api:
        with pytest.raises(CalcomError, match="invalid_response"):
            api.available_slots(1, START, END)


def test_invalid_arguments_fail_before_network():
    def unexpected(request):
        pytest.fail("Invalid input reached transport")
    with client(unexpected) as api:
        for action in [lambda: api.available_slots(1, START.replace(tzinfo=None), END),
                       lambda: api.available_slots(1, END, START),
                       lambda: api.get_event_type(-1),
                       lambda: api.list_bookings(limit=101),
                       lambda: api.list_bookings(status="invented"),
                       lambda: api.list_bookings(after_start=END, before_end=START)]:
            with pytest.raises(ValueError):
                action()


@pytest.mark.parametrize('owner,diagnostic_status,expected', [
    ('different-owner', 200, 'account_mismatch'),
    ('demo', 200, 'not_found'),
    ('DEMO', 200, 'not_found'),
    (None, 200, 'not_found'),
    (None, 401, 'not_found'),
])
def test_event_detail_404_diagnoses_account_mismatch_without_exposing_identity(owner, diagnostic_status, expected):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == '/v2/me':
            assert request.headers['cal-api-version'] == '2024-06-11'
            return httpx.Response(diagnostic_status, json={'status': 'success', 'data': {'username': owner}})
        assert request.url.path == '/v2/event-types/1'
        return httpx.Response(404, json={'status': 'error'})
    with client(handler) as api, pytest.raises(CalcomError) as exc:
        api.get_event_type(1)
    assert exc.value.code == expected
    assert exc.value.status_code == 404
    assert 'different-owner' not in str(exc.value)
    assert exc.value.__context__ is None
    assert len(requests) == 2
    assert all(r.method == 'GET' for r in requests)


@pytest.mark.parametrize('message,code', [
    ('Invalid attendee email', 'attendee_email_rejected'),
    ('Time slot is not available', 'slot_unavailable'),
    ('Invalid recurrenceCount', 'recurrence_rejected'),
    ('Unrecognized validation error private-data', 'booking_validation_rejected'),
])
def test_booking_400_classifies_actionable_error_without_echoing_provider_text(message, code):
    with client(lambda r: httpx.Response(400,json={'error':{'message':message}})) as api:
        with pytest.raises(CalcomError) as exc:
            api.execute('create', {'eventTypeId':1,'start':'2026-11-17T18:30:00Z',
                                  'attendee':{'name':'Team','email':'team@example.test','timeZone':'UTC'}})
    assert exc.value.code == code
    assert 'private-data' not in str(exc.value)


def test_cancelled_history_with_deleted_template_does_not_break_listing():
    rows=[BOOKING,{**BOOKING,'uid':'deleted-template','status':'cancelled','eventTypeId':None}]
    with client(lambda r:success(rows,pagination={'hasMore':False})) as api:
        bookings=api.list_bookings()
    assert len(bookings)==2
    assert next(b for b in bookings if b.uid=='deleted-template').event_type_id is None
    rows[1]['status']='accepted'
    with client(lambda r:success(rows,pagination={'hasMore':False})) as api:
        with pytest.raises(CalcomError,match='invalid_response'): api.list_bookings()


def test_provider_cannot_receive_mail_is_deliverability_not_syntax_error():
    with client(lambda r:httpx.Response(400,json={'error':{'message':'This email address cannot receive mail. Please use a valid email.'}})) as api:
        with pytest.raises(CalcomError) as exc:
            api.execute('create',{'eventTypeId':1,'start':START.isoformat(),
                'attendee':{'name':'Test','email':'demo@calendar-agent.invalid','timeZone':'UTC'}})
    assert exc.value.code=='attendee_email_rejected'
    assert exc.value.details['validation_reasons']==['email_undeliverable']


def test_booking_duplicates_within_and_across_pages_are_collapsed_by_uid():
    pages = iter([
        success([BOOKING, BOOKING], pagination={'hasMore': True, 'nextCursor': 'next'}),
        success([BOOKING, {**BOOKING, 'uid': 'separate-booking'}], pagination={'hasMore': False}),
    ])
    with client(lambda request: next(pages)) as api:
        rows = api.list_bookings()
    assert {row.uid for row in rows} == {'synthetic-uid', 'separate-booking'}
    assert len(rows) == 2


def test_conflicting_rows_for_same_booking_fail_instead_of_selecting_stale_data():
    with client(lambda request: success([BOOKING, {**BOOKING, 'status': 'cancelled'}],
                                       pagination={'hasMore': False})) as api:
        with pytest.raises(CalcomError, match='invalid_response'):
            api.list_bookings()

from copy import deepcopy
import json

import httpx
import pytest

from calcom_agent.client import CalcomClient, CalcomError
from calcom_agent.config import Settings

PAYLOAD = {'eventTypeId': 1, 'start': '2026-11-16T17:30:00Z',
           'attendee': {'name': 'David', 'email': 'david@example.test', 'timeZone': 'America/Los_Angeles'},
           'bookingFieldsResponses': {'title': 'David/Edward 1:1'}}
BOOKING = {'uid': 'verified-booking', 'title': 'David/Edward 1:1', 'status': 'accepted',
           'eventTypeId': 1, 'start': PAYLOAD['start'], 'end': '2026-11-16T18:00:00Z',
           'attendees': [PAYLOAD['attendee']]}


def success(data):
    return httpx.Response(200, json={'status': 'success', 'data': data})


def transport(mutation=None, google_mutation=None, refs=True, read_fails=False):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert json.loads(request.content) == PAYLOAD
            return success(deepcopy(BOOKING))
        path = request.url.path
        if path.endswith('/references'):
            return success([{'type': 'google_calendar', 'eventUid': 'google-event'}] if refs else [])
        if path == '/v2/event-types/1':
            return success({'id': 1, 'title': '30 min meeting', 'lengthInMinutes': 30})
        if path == '/v2/calendars/google/event/google-event':
            data = {'title': BOOKING['title'], 'start': {'time': BOOKING['start']}, 'end': {'time': BOOKING['end']}}
            data.update(google_mutation or {})
            return success(data)
        assert path == '/v2/bookings/verified-booking'
        if read_fails:
            return httpx.Response(503)
        data = deepcopy(BOOKING)
        data.update(mutation or {})
        return success(data)
    return httpx.MockTransport(handler), requests


def test_create_is_read_back_and_verified_in_google():
    mock, requests = transport()
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=mock) as c:
        result = c.execute('create', PAYLOAD)
    assert result['verification'] == {'calcom': 'verified', 'google_calendar': {'status': 'verified'}}
    assert [r.method for r in requests].count('POST') == 1
    assert requests[1].url.path == '/v2/bookings/verified-booking'


@pytest.mark.parametrize('mutation', [
    {'title': 'Wrong title'}, {'start': '2026-11-16T18:30:00Z', 'end': '2026-11-16T19:00:00Z'},
    {'end': '2026-11-16T18:30:00Z'}, {'eventTypeId': 2}, {'status': 'cancelled'}, {'attendees': []},
])
def test_read_back_mismatch_never_reports_success_or_retries(mutation):
    mock, requests = transport(mutation=mutation)
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=mock) as c:
        with pytest.raises(CalcomError, match='verification_failed') as exc:
            c.execute('create', PAYLOAD)
    assert exc.value.booking_uid == BOOKING['uid']
    assert sum(r.method == 'POST' for r in requests) == 1


def test_read_back_failure_preserves_reference_without_repeating_post():
    mock, requests = transport(read_fails=True)
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=mock) as c:
        with pytest.raises(CalcomError, match='verification_failed') as exc:
            c.execute('create', PAYLOAD)
    assert exc.value.booking_uid == BOOKING['uid']
    assert sum(r.method == 'POST' for r in requests) == 1


@pytest.mark.parametrize('kwargs,reason', [
    ({'refs': False}, 'no_google_reference'),
    ({'google_mutation': {'title': 'Stale title'}}, 'google_event_mismatch'),
])
def test_calcom_success_is_distinct_from_unverified_google_sync(kwargs, reason):
    mock, _ = transport(**kwargs)
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=mock) as c:
        result = c.execute('create', PAYLOAD)
    assert result['verification']['calcom'] == 'verified'
    assert result['verification']['google_calendar'] == {'status': 'unverified', 'reason': reason}


@pytest.mark.parametrize('wrong', [
    {'title': 'Default template title'}, {'end': '2026-11-16T18:30:00Z'},
    {'attendees': [{'email': 'wrong@example.test'}]},
])
def test_identical_post_and_get_still_must_match_intended_operation(wrong):
    data = {**deepcopy(BOOKING), **wrong}
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == '/v2/event-types/1':
            return success({'id': 1, 'title': '30 min meeting', 'lengthInMinutes': 30})
        return success(data)
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CalcomError, match='verification_failed'):
            c.execute('create', PAYLOAD)
    assert sum(r.method == 'POST' for r in requests) == 1


def test_google_404_after_cancel_is_not_treated_as_proof_of_deletion():
    def handler(request):
        return httpx.Response(404)
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as c:
        check = c.verify_google_calendar(BOOKING, cancelled=True,
            references=[{'type': 'google_calendar', 'eventUid': 'google-event'}])
    assert check == {'status': 'unverified', 'reason': 'google_reference_removed'}


@pytest.mark.parametrize('honor_duration', [True, False])
def test_reschedule_verifies_requested_duration_even_when_post_and_get_agree(honor_duration):
    requests = []
    changed = {**BOOKING, 'uid': 'rescheduled-booking',
               'end': '2026-11-16T18:30:00Z' if honor_duration else BOOKING['end']}
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert json.loads(request.content)['lengthInMinutes'] == 60
            assert request.headers['cal-api-version'] == '2026-02-25'
            return success(changed)
        if request.url.path.endswith('/references'):
            return success([])
        return success(BOOKING if request.url.path.endswith('/verified-booking') else changed)
    with CalcomClient(Settings('synthetic', 'demo'), transport=httpx.MockTransport(handler)) as c:
        payload = {'bookingUid': BOOKING['uid'], 'start': BOOKING['start'], 'lengthInMinutes': 60}
        if honor_duration:
            assert c.execute('reschedule', payload)['verification']['calcom'] == 'verified'
        else:
            with pytest.raises(CalcomError, match='verification_failed'):
                c.execute('reschedule', payload)
    assert sum(r.method == 'POST' for r in requests) == 1


def test_cancelled_google_event_is_never_verified_as_active():
    mock, _ = transport(google_mutation={'status': 'cancelled'})
    with CalcomClient(Settings('synthetic', 'demo'), transport=mock) as c:
        assert c.verify_google_calendar(BOOKING) == {
            'status': 'unverified', 'reason': 'google_event_cancelled'}


@pytest.mark.parametrize('tool_name', ['list_bookings', 'search_bookings'])
def test_listing_excludes_stale_calcom_booking_with_cancelled_google_event(tool_name):
    from unittest.mock import Mock
    from datetime import datetime, timezone
    from calcom_agent.conversation import Conversation
    from calcom_agent.model import ToolCall
    rows = [BOOKING, {**BOOKING, 'uid': 'active-booking', 'title': 'Active meeting'}]
    def handler(request):
        path = request.url.path
        if path == '/v2/bookings':
            return httpx.Response(200, json={
                'status': 'success', 'data': rows, 'pagination': {'hasMore': False}})
        if path.endswith('/references'):
            event_id = 'cancelled-event' if '/verified-booking/' in path else 'active-event'
            return success([{'type': 'google_calendar', 'eventUid': event_id}])
        cancelled = path.endswith('/cancelled-event')
        return success({'title': BOOKING['title'] if cancelled else 'Active meeting',
                        'status': 'cancelled' if cancelled else 'accepted',
                        'start': {'time': BOOKING['start']}, 'end': {'time': BOOKING['end']}})
    with CalcomClient(Settings('synthetic', 'demo'), transport=httpx.MockTransport(handler)) as c:
        chat = Conversation(Mock(), c, clock=lambda: datetime(2026, 11, 13, tzinfo=timezone.utc))
        result = chat._dispatch(ToolCall(tool_name, {
            'start': '2026-11-16T00:00:00', 'end': '2026-11-17T00:00:00'}))
    assert [row['uid'] for row in result['bookings']] == ['active-booking']
    assert result['bookings'][0]['number'] == 1
    assert result['cancelled_google_events_excluded'] == 1


@pytest.mark.parametrize('operation,reason_key', [('cancel','cancellationReason'), ('reschedule','reschedulingReason')])
def test_empty_optional_mutation_reasons_are_omitted_from_http_body(operation, reason_key):
    requests=[]
    def handler(request):
        requests.append(request)
        if request.method=='POST':
            assert reason_key not in json.loads(request.content)
            return success({**BOOKING, 'status':'cancelled'} if operation=='cancel' else BOOKING)
        if request.url.path.endswith('/references'):return success([])
        posted=any(r.method=='POST' for r in requests)
        return success({**BOOKING,'status':'cancelled'} if operation=='cancel' and posted else BOOKING)
    with CalcomClient(Settings('synthetic','demo'),transport=httpx.MockTransport(handler)) as c:
        payload={'bookingUid':BOOKING['uid'],reason_key:''}
        if operation=='reschedule':payload['start']=BOOKING['start']
        c.execute(operation,payload)


def test_overlap_flag_reaches_create_endpoint_without_other_overrides():
    sent=[]
    def handler(request):
        if request.method=='POST':sent.append(json.loads(request.content))
        if request.url.path.endswith('/references'):return success([])
        if request.url.path=='/v2/event-types/1':return success({'id':1,'title':'Demo','lengthInMinutes':30})
        return success(BOOKING)
    with CalcomClient(Settings('synthetic','demo'),transport=httpx.MockTransport(handler)) as c:
        c.execute('create',{**PAYLOAD,'allowConflicts':True})
    assert sent==[{**PAYLOAD,'allowConflicts':True}]


@pytest.mark.parametrize('calendar_failure',[False,True])
def test_conflict_check_is_nonblocking_and_preserves_both_bookings(calendar_failure):
    requests=[]
    existing={**BOOKING,'uid':'existing','title':'Other appointment'}
    def handler(request):
        requests.append(request)
        path=request.url.path
        if path=='/v2/event-types/1':return success({'id':1,'title':'Demo','lengthInMinutes':30})
        if path=='/v2/bookings' and request.method=='GET':
            if calendar_failure:return httpx.Response(503)
            return httpx.Response(200,json={'status':'success','data':[existing],'pagination':{'hasMore':False}})
        if path.endswith('/references'):return success([])
        if request.method=='POST':
            assert json.loads(request.content)['allowConflicts'] is True
        return success(BOOKING)
    with CalcomClient(Settings('synthetic','demo'),transport=httpx.MockTransport(handler)) as c:
        result=c.execute('create',{**PAYLOAD,'allowConflicts':True})
    assert result['verification']['calcom']=='verified'
    assert result['conflicts']['status']==('unknown' if calendar_failure else 'detected')
    assert sum(r.method=='POST' for r in requests)==1
    assert not any(r.method in {'DELETE','PATCH'} for r in requests)

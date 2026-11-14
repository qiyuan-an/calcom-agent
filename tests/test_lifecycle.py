from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from calcom_agent.client import CalcomClient, CalcomError
from calcom_agent.config import Settings
from calcom_agent.scheduling import SchedulingService, SchedulingError

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
START = NOW + timedelta(days=2)


def test_confirmed_lifecycle_tracks_changed_uid_and_cleans_up():
    state = {}
    posts = []
    def handler(request):
        path = request.url.path
        if request.method == 'POST':
            assert request.headers['cal-api-version'] == '2026-02-25'
            assert request.headers['Authorization'] == 'Bearer synthetic-key'
            body = json.loads(request.content)
            posts.append((path, body))
            if path == '/v2/bookings':
                assert body['attendee']['email'] == 'demo@example.test'
                state.update(uid='created', title='Demo', status='accepted', start=body['start'],
                             end=(START + timedelta(minutes=30)).isoformat(), eventTypeId=1, attendees=[body['attendee']])
            elif path == '/v2/bookings/created/reschedule':
                assert 'bookingUid' not in body
                assert body['reschedulingReason'] == 'Demo move'
                state.update(uid='moved', start=body['start'], end=(START + timedelta(hours=1, minutes=30)).isoformat())
            else:
                assert path == '/v2/bookings/moved/cancel'
                assert body == {'cancellationReason': 'Demo cleanup'}
                state['status'] = 'cancelled'
            return httpx.Response(201 if path == '/v2/bookings' else 200, json={'status': 'success', 'data': dict(state)})
        if path.endswith('/references'):
            data = []
        elif path == '/v2/event-types/1':
            data = {'id': 1, 'title': 'Demo', 'lengthInMinutes': 30}
        elif path == '/v2/slots':
            data = {'day': [{'start': request.url.params['start'], 'end': request.url.params['end']}]}
        elif path == '/v2/bookings':
            return httpx.Response(200, json={'status': 'success', 'data': [dict(state)] if state else [], 'pagination': {'hasMore': False}})
        else:
            assert path in {'/v2/bookings/created', '/v2/bookings/moved'}
            data = dict(state)
        return httpx.Response(200, json={'status': 'success', 'data': data})
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as client:
        service = SchedulingService(client, clock=lambda: NOW)
        proposal = service.prepare_create(1, START, name='Demo', email='demo@example.test', time_zone='UTC')
        assert not posts
        created = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
        assert client.list_bookings()[0].uid == created['uid'] == 'created'
        proposal = service.prepare_reschedule(created['uid'], START + timedelta(hours=1), reason='Demo move')
        moved = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
        assert moved['uid'] == client.list_bookings()[0].uid == 'moved'
        proposal = service.prepare_cancel(moved['uid'], reason='Demo cleanup')
        cancelled = service.execute_confirmed(proposal.token, confirmed=True, executor=client)
        assert cancelled['status'] == client.get_booking('moved')['status'] == 'cancelled'
        with pytest.raises(SchedulingError):
            service.execute_confirmed(proposal.token, confirmed=True, executor=client)
        assert len(posts) == 3


@pytest.mark.parametrize('failure', ['timeout', 'server', 'malformed'])
def test_unknown_write_outcome_never_retries_or_exposes_provider_data(failure):
    requests = []
    def handler(request):
        requests.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('synthetic-secret', request=request)
        if failure == 'server':
            return httpx.Response(503, text='private provider data')
        return httpx.Response(201, json={'status': 'success', 'data': {'uid': 'incomplete'}})
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CalcomError, match='write_outcome_unknown') as exc:
            client.execute('create', {'eventTypeId': 1, 'start': START.isoformat(), 'attendee': {'name': 'Demo', 'email': 'demo@example.test', 'timeZone': 'UTC'}})
        assert exc.value.__context__ is None
        assert len(requests) == 1


def test_conflict_and_invalid_write_are_not_reported_as_success():
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'status': 'success', 'data': {'uid': 'demo', 'title': 'Demo', 'status': 'accepted', 'eventTypeId': 1, 'start': START.isoformat(), 'end': (START + timedelta(minutes=30)).isoformat()}})
        return httpx.Response(409, text='private conflict')
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CalcomError, match='http_error'):
            client.execute('reschedule', {'bookingUid': 'demo', 'start': START.isoformat()})
        for operation, payload in [('invented', {}), ('cancel', {'bookingUid': '../other'}),
                                   ('create', {'allowConflicts': True})]:
            with pytest.raises(ValueError):
                client.execute(operation, payload)
        assert len(calls) == 2

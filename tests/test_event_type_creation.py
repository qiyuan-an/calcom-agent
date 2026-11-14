from dataclasses import asdict, replace
from datetime import datetime, timezone
from unittest.mock import Mock
import json

import httpx
import pytest

from calcom_agent.client import CalcomClient, CalcomError, EventType, ONE_ON_ONE_NAME
from calcom_agent.config import Settings
from calcom_agent.conversation import Conversation
from calcom_agent.model import ModelReply, ToolCall
from calcom_agent.scheduling import SchedulingService, SchedulingError

NOW = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
EVENT = EventType(42, 'Intro', 45, [], [{'type': 'integration', 'integration': 'cal-video'}], slug='intro-45')


def call(tool, **args):
    return ModelReply(calls=(ToolCall(tool, args),))


def test_chat_creates_template_then_books_using_returned_id_with_separate_confirmations():
    requests, state = [], {}
    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == '/v2/event-types':
            if request.method == 'GET':
                data = [state['event']] if 'event' in state else []
            else:
                assert request.method == 'POST'
                assert request.headers['cal-api-version'] == '2026-06-12'
                assert request.headers['Authorization'] == 'Bearer synthetic-key'
                body = json.loads(request.content)
                assert body == {'title': 'Intro', 'slug': 'intro-45', 'lengthInMinutes': 45,
                                'locations': [{'type': 'integration', 'integration': 'cal-video'}], 'customName': ONE_ON_ONE_NAME}
                state['event'] = {'id': 42, **body}
                data = state['event']
        elif path == '/v2/event-types/42':
            data = state['event']
        elif path.endswith('/references'):
            data = []
        elif path == '/v2/bookings/demo':
            data = state['booking']
        elif path == '/v2/slots':
            assert request.url.params['eventTypeId'] == '42'
            data = {'day': [{'start': request.url.params['start'], 'end': request.url.params['end']}]}
        else:
            assert path == '/v2/bookings' and request.method == 'POST'
            body = json.loads(request.content)
            assert body['eventTypeId'] == 42
            data = {'uid': 'demo', 'title': 'Intro', 'status': 'accepted', 'eventTypeId': 42,
                    'start': body['start'], 'end': '2026-11-13T17:45:00Z', 'attendees': [body['attendee']]}
            state['booking'] = data
        return httpx.Response(201 if request.method == 'POST' else 200, json={'status': 'success', 'data': data})
    model = Mock()
    model.chat.side_effect = [call('list_event_types'),
        call('prepare_create_event_type', title='Intro', slug='intro-45', duration=45),
        call('available_slots', event_type_id=42, start='2026-11-13T09:00:00', end='2026-11-13T09:45:00', duration=45),
        call('prepare_create', event_type_id=42, start='2026-11-13T09:00:00', name='Demo', email='demo@example.test', duration=45)]
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as client:
        chat = Conversation(model, client, executor=client, clock=lambda: NOW, allow_conflicts=False)
        assert 'template only' in chat.turn('Book a 45-minute intro tomorrow at 9 for Demo, demo@example.test')
        assert not [r for r in requests if r.method == 'POST']
        assert 'No booking has been created' in chat.turn('confirm')
        assert 42 in chat.events
        assert len([r for r in requests if r.method == 'POST']) == 1
        assert 'demo@example.test' in chat.turn('Continue with that booking')
        assert len([r for r in requests if r.method == 'POST']) == 1
        assert 'accepted' in chat.turn('confirm')
        assert [r.url.path for r in requests if r.method == 'POST'] == ['/v2/event-types', '/v2/bookings']


def test_reuse_slug_conflict_and_stale_discovery_block_creation():
    client, executor = Mock(), Mock()
    service = SchedulingService(client, clock=lambda: NOW)
    client.list_event_types.return_value = [EVENT]
    with pytest.raises(SchedulingError, match='reuse_existing'):
        service.prepare_create_event_type(title='Another', slug='another', duration=45)
    with pytest.raises(SchedulingError, match='slug_exists'):
        service.prepare_create_event_type(title='Other', slug='intro-45', duration=60)
    client.list_event_types.return_value = []
    proposal = service.prepare_create_event_type(title='Intro', slug='intro-45', duration=45)
    client.list_event_types.return_value = [EVENT]
    with pytest.raises(SchedulingError):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    executor.execute.assert_not_called()
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)


def test_template_validation_failed_discovery_and_confirmation_are_safe():
    client, executor = Mock(), Mock()
    client.list_event_types.return_value = []
    service = SchedulingService(client, clock=lambda: NOW)
    for args in [{'title': '', 'slug': 'demo', 'duration': 45},
                 {'title': 'Demo', 'slug': '../demo', 'duration': 45},
                 {'title': 'Demo', 'slug': 'demo', 'duration': True}]:
        with pytest.raises(SchedulingError):
            service.prepare_create_event_type(**args)
    client.list_event_types.side_effect = CalcomError('unauthorized')
    with pytest.raises(CalcomError):
        service.prepare_create_event_type(title='Intro', slug='intro-45', duration=45)
    client.list_event_types.side_effect = None
    proposal = service.prepare_create_event_type(title='Intro', slug='intro-45', duration=45)
    with pytest.raises(SchedulingError, match='confirmation_required'):
        service.execute_confirmed(proposal.token, confirmed=False, executor=executor)
    executor.execute.return_value = {'eventType': asdict(EVENT)}
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_count == 1


@pytest.mark.parametrize('response', [httpx.Response(503, text='private provider data'),
    httpx.Response(201, json={'status': 'success', 'data': {'id': 0}})])
def test_uncertain_template_post_not_retried_or_reported_as_created(response):
    requests = []
    def handler(request):
        requests.append(request)
        return response
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CalcomError, match='write_outcome_unknown') as exc:
            client.execute('create_event_type', {'title': 'Intro', 'slug': 'intro-45', 'lengthInMinutes': 45,
                                               'locations': [{'type': 'integration', 'integration': 'cal-video'}]})
        assert exc.value.__context__ is None
    assert len(requests) == 1

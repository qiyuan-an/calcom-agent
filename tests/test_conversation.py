from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import httpx
import pytest

from calcom_agent.client import Booking, EventType, Slot, CalcomError
from calcom_agent.conversation import Conversation
from calcom_agent.model import ModelError, ModelReply, ToolCall
from calcom_agent.ollama import OllamaProvider

NOW = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
START = datetime(2026, 11, 13, 17, tzinfo=timezone.utc)
EVENT = EventType(1, 'Demo', 30, [], [])
BOOKING = Booking('demo-uid', 'Demo', 'accepted', START, START + timedelta(minutes=30), 1)


def call(tool_name, **args):
    return ModelReply(calls=(ToolCall(tool_name, args),))


class Scripted:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []
    def chat(self, messages, tools):
        self.requests.append(deepcopy(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def session(model, **kwargs):
    kwargs.setdefault("allow_conflicts", False)  # Legacy strict-availability cases.
    client = Mock()
    client.list_event_types.return_value = [EVENT]
    client.get_event_type.return_value = EVENT
    client.list_bookings.return_value = [BOOKING]
    client.available_slots.side_effect = lambda _, start, end, **kw: [Slot(start, end)]
    executor = Mock()
    executor.execute.return_value = {'status': 'accepted', 'uid': 'synthetic-result', 'verification': {'calcom': 'verified'}}
    return Conversation(model, client, executor=executor, clock=lambda: NOW, **kwargs), client, executor


def test_list_followup_second_booking_reschedule_and_confirmation_bypasses_model():
    model = Scripted(call('list_bookings'), ModelReply('Two bookings.'),
                     call('prepare_reschedule', number=2, start='2026-11-13T11:00:00'))
    chat, client, executor = session(model)
    client.list_bookings.return_value = [BOOKING, Booking('other', 'Other', 'accepted', START + timedelta(hours=1), START + timedelta(hours=1, minutes=30), 1)]
    assert '2. Other' in chat.turn('What is on my calendar?')
    answer = chat.turn('Move the second one to 11 tomorrow')
    assert 'Move Other' in answer and "Reply 'confirm'" in answer
    executor.execute.assert_not_called()
    assert 'Cal.com returned the reschedule result' in chat.turn('confirm')
    assert executor.execute.call_args.args == ('reschedule', {'bookingUid': 'other', 'start': '2026-11-13T19:00:00Z', 'reschedulingReason': ''})
    assert len(model.requests) == 3
    assert 'Current local time: 2026-11-12T08:00:00-08:00' in model.requests[0][0]['content']
    assert any(m.get('content') == 'Move the second one to 11 tomorrow' for m in model.requests[-1])


def test_create_missing_information_followup_correction_and_duplicate_confirm():
    model = Scripted(call('list_event_types'), call('prepare_create', event_type_id=1, start='2026-11-13T09:00:00'),
                     ModelReply('What name and email should I use?'),
                     call('prepare_create', event_type_id=1, start='2026-11-13T09:00:00', name='Demo', email='demo@example.test'),
                     call('prepare_create', event_type_id=1, start='2026-11-13T10:00:00', name='Demo', email='demo@example.test'),
                     ModelReply('There is no pending confirmation.'))
    chat, _, executor = session(model)
    answer = chat.turn('Book an intro tomorrow')
    assert 'attendee name' in answer and 'attendee email address' in answer
    assert 'missing_information' in json.dumps(model.requests[2])
    chat.turn('Demo, demo@example.test')
    old = chat.pending.token
    chat.turn('Actually make it 10am')
    assert chat.pending.token != old
    assert any(m.get('content') == 'Demo, demo@example.test' for m in model.requests[-1])
    chat.turn('yes')
    assert executor.execute.call_args.args[1]['start'] == '2026-11-13T18:00:00Z'
    chat.turn('confirm')
    assert executor.execute.call_count == 1


def test_cancel_and_no_executor_are_explicit():
    chat, _, executor = session(Scripted(call('list_bookings'), call('prepare_cancel', number=1)))
    chat.turn('Cancel my booking')
    chat.turn('yes')
    assert executor.execute.call_args.args[0] == 'cancel'
    chat, _, _ = session(Scripted(call('list_bookings'), call('prepare_cancel', number=1)))
    chat.executor = None
    chat.turn('Cancel my booking')
    assert 'not configured' in chat.turn('yes')
    assert chat.pending is None


@pytest.mark.parametrize('bad', [call('execute', confirmed=True), call('prepare_cancel', number=True),
    call('list_bookings', unexpected='value'), call('prepare_reschedule', number=1),
    call('prepare_create', event_type_id=999, start='2026-11-13T09:00:00'),
    ModelReply(calls=(ToolCall('list_bookings', {}), ToolCall('prepare_cancel', {'number': 1})))])
def test_invalid_tool_calls_never_reach_executor_and_allow_recovery(bad):
    model = Scripted(bad, ModelReply('Please clarify.'))
    chat, _, executor = session(model)
    answer = chat.turn('Request')
    assert answer == ('Please provide: meeting date and time.'
                      if bad.calls[0].name == 'prepare_reschedule' else
                      'The meeting template ID has not been verified. The assistant should refresh event types and use an ID returned by Cal.com.'
                      if bad.calls[0].name == 'prepare_create' else 'Please clarify.')
    executor.execute.assert_not_called()
    assert 'error' in model.requests[1][-1]['content']


def test_bounded_loop_model_and_api_failure_reset():
    chat, _, executor = session(Scripted(call('list_event_types'), call('list_event_types')), max_steps=2)
    assert 'No booking was changed' in chat.turn('Request')
    executor.execute.assert_not_called()
    chat, client, _ = session(Scripted(call('list_bookings'), ModelReply('Could not fetch bookings.'), ModelError('model_timeout')))
    client.list_bookings.side_effect = CalcomError('unauthorized')
    assert 'Could not fetch' in chat.turn('List')
    assert 'model_timeout' in chat.turn('Try again')
    assert chat.history
    chat.reset()
    assert chat.history == [] and chat.candidates == [] and chat.events == {} and chat.pending is None


def test_local_day_overlap_includes_booking_crossing_midnight():
    chat, client, _ = session(Scripted(call('list_bookings', start='2026-11-13T00:00:00', end='2026-11-14T00:00:00'), ModelReply('Done')))
    client.list_bookings.return_value = [Booking('overlap', 'Demo', 'accepted', datetime(2026,11,13,7,30,tzinfo=timezone.utc), datetime(2026,11,13,8,30,tzinfo=timezone.utc), 1)]
    chat.turn('Tomorrow')
    assert chat.candidates[0].uid == 'overlap'


def test_ollama_wire_contract_and_no_credentials_or_thinking_forwarded():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': '', 'thinking': 'private internal text',
            'tool_calls': [{'function': {'name': 'list_bookings', 'arguments': {}}}]}})
    adapter = OllamaProvider('configured-model', 'http://localhost:11434', transport=httpx.MockTransport(handler))
    try:
        reply = adapter.chat([{'role': 'user', 'content': 'List bookings'}], [])
    finally:
        adapter.close()
    body = json.loads(requests[0].content)
    assert requests[0].url.path == '/api/chat'
    assert body['model'] == 'configured-model' and body['stream'] is False and body['think'] is False
    assert 'Authorization' not in requests[0].headers
    assert reply == call('list_bookings')


@pytest.mark.parametrize('response', [httpx.Response(500, text='sensitive'), httpx.Response(200, text='bad json'),
    httpx.Response(200, json={'done': False, 'message': {'role': 'assistant'}}),
    httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'tool_calls': [{'function': {'name': 'x', 'arguments': 'bad'}}]}})])
def test_bad_model_responses_are_sanitized(response):
    adapter = OllamaProvider('demo', transport=httpx.MockTransport(lambda r: response))
    try:
        with pytest.raises(ModelError) as exc:
            adapter.chat([], [])
        assert 'sensitive' not in str(exc.value) and exc.value.__context__ is None
    finally:
        adapter.close()


def test_ollama_transport_failure_does_not_retain_raw_context():
    def handler(request):
        raise httpx.ConnectError('sensitive', request=request)
    adapter = OllamaProvider('demo', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError, match='model_connection_failed') as exc:
            adapter.chat([], [])
        assert exc.value.__context__ is None
    finally:
        adapter.close()


@pytest.mark.parametrize("status", [400, 404, 429, 500])
def test_ollama_http_status_is_visible_without_provider_details(status):
    adapter = OllamaProvider('demo', transport=httpx.MockTransport(
        lambda r: httpx.Response(status, json={'error': 'synthetic-secret private prompt'})))
    try:
        with pytest.raises(ModelError) as exc:
            adapter.chat([], [])
        assert str(exc.value) == f'model_http_error_{status}'
        assert exc.value.__context__ is None
    finally:
        adapter.close()


def test_availability_uses_discovered_event_and_saved_booking_number():
    model = Scripted(call('list_event_types'), call('list_bookings'),
        call('available_slots', event_type_id=1, start='2026-11-13T10:00:00', end='2026-11-13T12:00:00', duration=30, number=1),
        ModelReply('Here are the available slots.'))
    chat, client, executor = session(model)
    assert 'available slots' in chat.turn('Find a later time for the first booking')
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] == 'demo-uid'
    assert client.available_slots.call_args.args[1].hour == 18
    assert 'slots' in chat.history[-2]['content']
    executor.execute.assert_not_called()


def test_invalid_local_time_returns_error_then_model_can_clarify():
    model = Scripted(call('list_event_types'),
                     call('prepare_create', event_type_id=1, start='2027-03-14T02:30:00', name='Demo', email='demo@example.test'),
                     ModelReply('That time does not exist. Which other time would you like?'))
    chat, _, executor = session(model)
    assert 'does not exist' in chat.turn('Book at 2:30 on the spring DST change for demo@example.test')
    assert 'nonexistent_local_time' in model.requests[-1][-1]['content']
    executor.execute.assert_not_called()


def test_model_resolves_tomorrow_using_supplied_clock_and_proposal_exposes_absolute_date():
    model = Scripted(call('list_bookings'),
                     call('prepare_reschedule', number=1, start='2026-11-13T11:00:00'))
    chat, _, executor = session(model)
    answer = chat.turn('Move my first booking to tomorrow at 11am')
    assert 'Tomorrow: 2026-11-13' in model.requests[-1][0]['content']
    assert '2026-11-13T19:00:00Z' in answer
    executor.execute.assert_not_called()


def test_model_supplies_explicit_local_day_bounds_for_tomorrow():
    chat, client, _ = session(Scripted(call('list_bookings', start='2026-11-13T00:00:00', end='2026-11-14T00:00:00'), ModelReply('Done')))
    client.list_bookings.return_value = [BOOKING,
        Booking('later', 'Later', 'accepted', START + timedelta(days=2), START + timedelta(days=2, minutes=30), 1)]
    chat.turn('List my bookings tomorrow')
    assert [booking.uid for booking in chat.candidates] == ['demo-uid']


@pytest.mark.parametrize('endpoint', ['http://localhost:11434', 'http://localhost:11434/api',
    'http://localhost:11434/api/chat/', 'http://localhost:11434/v1'])
def test_ollama_common_endpoint_forms_do_not_duplicate_api_path(endpoint):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': 'OK'}})
    adapter = OllamaProvider('demo', endpoint, transport=httpx.MockTransport(handler))
    try:
        assert adapter.chat([], []).content == 'OK'
        assert paths == ['/api/chat']
    finally:
        adapter.close()


def test_ollama_missing_model_diagnosis_uses_metadata_without_echoing_details():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.method == 'POST':
            return httpx.Response(404, json={'error': 'private prompt or configuration'})
        return httpx.Response(200, json={'models': [{'name': 'installed:tag'}]})
    adapter = OllamaProvider('missing', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError, match='^model_not_installed$'):
            adapter.chat([], [])
        assert paths == ['/api/chat', '/api/ps', '/api/tags']
    finally:
        adapter.close()


@pytest.mark.parametrize('installed,status', [(True, 'ready'), (False, 'model_not_installed')])
def test_connection_diagnostic_uses_session_endpoint_without_generation(installed, status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'models': [{'name': 'demo'}] if installed else []})
    adapter = OllamaProvider('demo', 'http://localhost:11434/api/chat', transport=httpx.MockTransport(handler))
    try:
        result = adapter.check_connection()
        assert result['status'] == status and result['model'] == 'demo'
        assert result['server'] == 'http://localhost:11434'
        assert len(requests) == 1 and requests[0].method == 'GET'
        assert requests[0].url.path == '/api/tags'
    finally:
        adapter.close()


def test_connection_diagnostic_failure_hides_raw_exception():
    def handler(request):
        raise httpx.ConnectError('private configuration', request=request)
    adapter = OllamaProvider('demo', transport=httpx.MockTransport(handler))
    try:
        assert adapter.check_connection()['status'] == 'model_connection_failed'
    finally:
        adapter.close()


def test_timeout_diagnostics_include_phase_timing_and_cpu_residency_without_retry():
    from calcom_agent.diagnostics import diagnostic
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert request.extensions['timeout']['read'] == 180
            assert request.extensions['timeout']['connect'] == 5
            raise httpx.ReadTimeout('private prompt', request=request)
        return httpx.Response(200, json={'models': [{'name': 'demo', 'size': 2**30, 'size_vram': 0}]})
    adapter = OllamaProvider('demo', timeout_seconds=180, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError) as exc:
            adapter.chat([{'role': 'user', 'content': 'private prompt'}], [])
        detail = diagnostic('model', str(exc.value), details=exc.value.details)
        assert detail['context']['timeout_phase'] == 'read'
        assert detail['context']['read_timeout_seconds'] == 180
        assert detail['context']['placement'] == 'cpu_only'
        assert detail['context']['message_count'] == 1
        assert 'CPU' in detail['next_step']
        assert 'private prompt' not in json.dumps(detail)
        assert [r.method for r in requests] == ['POST', 'GET']
    finally:
        adapter.close()


def test_server_failure_categories_and_diagnostic_allowlist():
    from calcom_agent.diagnostics import diagnostic
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(500, json={'error': 'CUDA memory allocation failed; private calendar data'})
        return httpx.Response(200, json={'models': []})
    adapter = OllamaProvider('demo', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError) as exc:
            adapter.chat([], [])
        detail = diagnostic('model', str(exc.value), details={**exc.value.details,
                            'raw_response': 'synthetic-secret', 'exception': 'secret', 'path': '/secret'})
        assert detail['http_status'] == 500
        assert set(detail['context']['server_error_categories']) == {'memory_allocation', 'gpu_or_device'}
        assert detail['context']['model_loaded'] is False
        assert 'private calendar data' not in json.dumps(detail) and 'synthetic-secret' not in json.dumps(detail)
        assert 'path' not in detail['context']
    finally:
        adapter.close()


def test_missing_fields_replace_vague_model_clarification():
    model = Scripted(call('list_event_types'),
        call('prepare_create', event_type_id=1, start='2026-11-16T09:30:00', name='David'),
        ModelReply('Please provide more details.'))
    chat, _, executor = session(model)
    answer = chat.turn('Book with David next Monday at 9:30 am')
    assert answer == 'Please provide: attendee email address.'
    executor.execute.assert_not_called()


def test_stale_event_404_refreshes_discovery_and_allows_new_id():
    replacement = EventType(2, 'Intro', 30, [], [])
    model = Scripted(call('list_event_types'),
        call('available_slots', event_type_id=1, start='2026-11-16T09:30:00', end='2026-11-16T10:00:00'),
        call('prepare_create', event_type_id=2, start='2026-11-16T09:30:00', name='David', email='david@example.test'))
    chat, client, executor = session(model)
    client.list_event_types.side_effect = [[EVENT], [replacement]]
    client.available_slots.side_effect = [CalcomError('not_found', 404),
        [Slot(datetime(2026, 11, 16, 17, 30, tzinfo=timezone.utc), datetime(2026, 11, 16, 18, tzinfo=timezone.utc))]]
    client.get_event_type.return_value = replacement
    answer = chat.turn('Book David next Monday at 9:30, david@example.test')
    assert 'Create Intro' in answer
    assert set(chat.events) == {2}
    recovery = json.loads(model.requests[2][-1]['content'])
    assert recovery['event_types'][0]['id'] == 2
    executor.execute.assert_not_called()


def test_discovery_404_explains_account_details_without_creating_template():
    chat, client, executor = session(Scripted(call('list_event_types'), ModelReply('Please clarify.')))
    client.list_event_types.side_effect = CalcomError('not_found', 404)
    answer = chat.turn('Book David next Monday at 9:30')
    assert 'CAL_USERNAME' in answer and 'API key' in answer
    assert 'No booking or template was created or changed' in answer
    executor.execute.assert_not_called()


@pytest.mark.parametrize('resource,expected,solution', [
    ('event_types', 'load your Cal.com meeting templates', 'public Cal.com profile'),
    ('event_type', 'details could not be loaded', 'Open Event Types'),
    ('availability', 'available times', 'public booking link'),
    ('bookings', 'booking needed', 'refresh bookings'),
])
def test_404_identifies_failed_step_and_relevant_solution(resource, expected, solution):
    model = Scripted(call('list_event_types'),
        call('available_slots', event_type_id=1, start='2026-11-16T09:30:00', end='2026-11-16T10:00:00'),
        ModelReply('Please clarify.'))
    chat, client, executor = session(model)
    client.available_slots.side_effect = CalcomError('not_found', 404, resource=resource)
    answer = chat.turn('Book David next Monday at 9:30')
    assert expected in answer and solution in answer
    if resource == 'availability':
        assert 'CAL_USERNAME' not in answer
    assert chat.diagnostics[-1]['summary'] == answer
    executor.execute.assert_not_called()


def test_existing_template_recovery_returns_choices_instead_of_vague_error():
    model = Scripted(call('prepare_create_event_type', title='Meeting', slug='meeting', duration=30),
                     ModelReply('Please clarify.'))
    chat, _, executor = session(model)
    answer = chat.turn('Book David next Monday at 9:30')
    assert 'Demo (30 minutes)' in answer and 'Which should I use?' in answer
    assert json.loads(model.requests[-1][-1]['content'])['event_types'][0]['id'] == 1
    executor.execute.assert_not_called()


def test_verified_account_mismatch_stops_loop_and_explains_configuration_fix():
    model = Scripted(call('list_event_types'),
        call('prepare_create', event_type_id=1, start='2026-11-16T09:30:00', name='David', email='david@example.test'))
    chat, client, executor = session(model)
    client.get_event_type.side_effect = CalcomError('account_mismatch', 404)
    answer = chat.turn('Book David next Monday at 9:30, david@example.test')
    assert 'different accounts' in answer
    assert 'CAL_USERNAME' in answer and 'Restart the app' in answer
    assert 'No booking or template was created or changed' in answer
    assert chat.events == {} and chat.candidates == [] and chat.pending is None
    assert len(model.requests) == 2
    executor.execute.assert_not_called()
    assert chat.diagnostics[-1]['code'] == 'account_mismatch'


def test_one_on_one_defaults_to_30_and_infers_title_without_asking():
    from calcom_agent.scheduling import ONE_ON_ONE_NAME
    event = EventType(2, '30 min meeting', 30, [{'slug': 'title', 'required': True}], [],
                      policy={'customName': ONE_ON_ONE_NAME})
    model = Scripted(call('list_event_types'),
        call('prepare_create', event_type_id=2, start='2026-11-16T09:30:00', name='David',
             email='david@example.test', duration=30))
    chat, client, executor = session(model)
    client.list_event_types.return_value = [EventType(1, '15 min meeting', 15, [], []), event]
    client.get_event_type.return_value = event
    client.get_profile.return_value = {'name': 'Edward An'}
    answer = chat.turn('Book a meeting with David next Monday at 9:30 am, david@example.test.')
    assert 'David/Edward 1:1' in answer and '30 minutes' in answer
    assert client.get_event_type.call_args.args == (2,)
    assert client.available_slots.call_args.kwargs['duration'] == 30
    executor.execute.assert_not_called()
    chat.turn('proceed')
    payload = executor.execute.call_args.args[1]
    assert payload['bookingFieldsResponses']['title'] == 'David/Edward 1:1'
    assert payload['eventTypeId'] == 2


def test_model_success_without_tools_cannot_claim_a_booking():
    chat, _, executor = session(Scripted(ModelReply('The meeting has been successfully scheduled with David.')))
    answer = chat.turn('Please book David next Monday at 9:30 am')
    assert 'No action was executed or verified' in answer
    assert 'successfully scheduled' not in answer
    executor.execute.assert_not_called()


def test_model_cannot_invent_attendee_email():
    model = Scripted(call('list_event_types'),
        call('prepare_create', start='2026-11-16T09:30:00', name='David', email='invented@example.test'),
        ModelReply('Please clarify.'))
    chat, client, executor = session(model)
    answer = chat.turn('Book David next Monday at 9:30 am')
    assert 'attendee email address' in answer
    client.get_event_type.assert_not_called()
    executor.execute.assert_not_called()


def test_empty_bookings_never_establish_google_calendar_availability():
    chat, client, _ = session(Scripted(call('list_bookings'), ModelReply('That confirms your calendar is empty.')))
    client.list_bookings.return_value = []
    answer = chat.turn('Is my calendar free next Monday?')
    assert 'does not prove your Google Calendar is empty' in answer


def test_unverified_executor_result_never_claims_completion():
    model = Scripted(call('list_event_types'),
        call('prepare_create', start='2026-11-16T09:30:00', name='David', email='david@example.test'))
    chat, _, executor = session(model)
    executor.execute.return_value = {'uid': 'unverified', 'status': 'accepted'}
    chat.turn('Book David next Monday at 9:30, david@example.test')
    assert 'completion has not been verified' in chat.turn('confirm')



def test_offset_bearing_model_time_is_normalized_without_reasking_user():
    model = Scripted(call('list_event_types'),
        call('prepare_create', start='2026-11-16T17:30:00Z', name='David', email='david@example.test'))
    chat, _, executor = session(model)
    answer = chat.turn('Book David next Monday at 9:30 am, david@example.test')
    assert '2026-11-16T09:30:00-08:00' in answer
    executor.execute.assert_not_called()


@pytest.mark.parametrize('tool_name', ['prepare_update', 'prepare_reschedule'])
def test_make_saved_seminar_one_hour_without_repeating_start(tool_name):
    from dataclasses import replace
    model = Scripted(call('list_bookings'), ModelReply('Which meeting?'),
                     call(tool_name, number=1, duration=60))
    chat, client, executor = session(model)
    client.get_event_type.return_value = replace(EVENT, policy={'lengthInMinutesOptions': [30, 60]})
    chat.turn('Find the seminar')
    answer = chat.turn('Make that seminar 1 hour')
    assert '60 minutes' in answer
    assert chat.pending is not None
    executor.execute.assert_not_called()
    chat.turn('confirm')
    assert executor.execute.call_args.args[1]['start'] == '2026-11-13T17:00:00Z'
    assert executor.execute.call_args.args[1]['lengthInMinutes'] == 60
    assert not chat.diagnostics


@pytest.mark.parametrize('tool_name', ['list_bookings', 'search_bookings'])
def test_duplicate_rows_do_not_duplicate_numbered_booking_references(tool_name):
    from dataclasses import replace
    chat, client, executor = session(Scripted())
    adjacent = replace(BOOKING, uid='adjacent', title='David/Edward 1:1',
                       start=BOOKING.end, end=BOOKING.end + timedelta(minutes=30))
    client.list_bookings.return_value = [BOOKING, BOOKING, adjacent, adjacent]
    args = {'start': '2026-11-13T00:00:00', 'end': '2026-11-14T00:00:00'}
    result = chat._dispatch(ToolCall(tool_name, args))
    assert [row['number'] for row in result['bookings']] == [1, 2]
    assert [row['uid'] for row in result['bookings']] == [BOOKING.uid, adjacent.uid]
    chat._dispatch(ToolCall('prepare_cancel', {'number': 2}))
    chat.turn('confirm')
    assert executor.execute.call_args.args[1]['bookingUid'] == adjacent.uid


def test_schedule_display_uses_fetched_records_and_converts_utc_to_pacific():
    from dataclasses import replace
    model = Scripted(call('list_bookings', start='2026-11-16T00:00:00', end='2026-11-17T00:00:00'),
                     ModelReply('1:1 with David at 17:00-08:00. Invented duplicate at 17:30.'))
    chat, client, _ = session(model)
    start = datetime(2026, 11, 16, 17, 30, tzinfo=timezone.utc)
    client.list_bookings.return_value = [replace(BOOKING, title='David/Edward 1:1',
        start=start, end=start + timedelta(minutes=30))]
    answer = chat.turn("What's my schedule next Monday?")
    assert '**Monday, November 16, 2026**' in answer
    assert 'America/Los_Angeles' in answer
    assert '9:30 AM–10:00 AM' in answer
    assert '2026-11-16T' not in answer
    assert answer.count('David/Edward 1:1') == 1
    assert 'Invented' not in answer and '17:00-08:00' not in answer
    assert len(chat.candidates) == 1


def test_afternoon_move_recovers_draft_and_uses_saved_template_without_discovery():
    draft = "Okay, let's see. I need to check available slots for the afternoon."
    model = Scripted(ModelReply(draft, incomplete=True),
        call('available_slots', number=1, event_type_id=1,
             start='2026-11-13T12:00:00', end='2026-11-13T17:00:00'),
        call('prepare_reschedule', number=1, start='2026-11-13T14:00:00'))
    chat, client, executor = session(model)
    chat.candidates = [BOOKING]
    answer = chat.turn('Can we reschedule it to afternoon?')
    assert draft not in answer and chat.model_outputs == [draft]
    assert client.available_slots.call_args.kwargs['duration'] == 30
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] == BOOKING.uid
    assert client.available_slots.call_args.args[1].hour == 20  # Noon Pacific in UTC.
    assert not chat.pending
    executor.execute.assert_not_called()
    assert 'Reply' in chat.turn('2pm please')
    assert chat.model_outputs == []
    executor.execute.assert_not_called()
    chat.turn('confirm')
    assert executor.execute.call_args.args[1]['start'] == '2026-11-13T22:00:00Z'


def test_ollama_discards_inline_thinking_and_flags_truncation():
    response = {'done': True, 'done_reason': 'length', 'message': {
        'role': 'assistant', 'content': '<think>private reasoning</think>Unfinished answer',
        'thinking': 'also private'}}
    with_provider = OllamaProvider('demo', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)))
    try:
        reply = with_provider.chat([], [])
    finally:
        with_provider.close()
    assert reply.content == 'Unfinished answer' and reply.incomplete


def test_availability_rejects_different_template_for_saved_booking():
    chat, client, _ = session(Scripted())
    chat.candidates = [BOOKING]
    chat.events[2] = EVENT
    with pytest.raises(Exception, match='booking_event_type_mismatch'):
        chat._dispatch(ToolCall('available_slots', {'number': 1, 'event_type_id': 2,
            'start': '2026-11-13T12:00:00', 'end': '2026-11-13T17:00:00'}))
    client.available_slots.assert_not_called()


def test_single_saved_booking_availability_infers_reference_for_read_only_check():
    chat, client, executor = session(Scripted())
    chat.candidates = [BOOKING]
    result = chat._dispatch(ToolCall('available_slots', {'event_type_id': 1,
        'start': '2026-11-13T12:00:00', 'end': '2026-11-13T17:00:00'}))
    assert result['rescheduling']
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] == BOOKING.uid
    assert client.available_slots.call_args.kwargs['duration'] == 30
    executor.execute.assert_not_called()


@pytest.mark.parametrize('replies,max_steps', [
    ([call('list_bookings')], 1),
    ([call('list_bookings'), ModelReply('Unfinished draft', incomplete=True)], 2),
])
def test_successful_listing_survives_generation_limit(replies, max_steps):
    chat, _, executor = session(Scripted(*replies), max_steps=max_steps)
    answer = chat.turn("What's my schedule next Monday?")
    assert 'Your Cal.com schedule' in answer and '1. Demo' in answer
    assert not any(d['code'] == 'tool_limit' for d in chat.diagnostics)
    executor.execute.assert_not_called()


def test_generation_repair_instructions_do_not_leak_into_next_turn():
    model = Scripted(ModelReply('unfinished', incomplete=True), call('list_bookings'),
                     ModelReply('Done'), ModelReply('Which date?'))
    chat, _, _ = session(model)
    assert 'Your Cal.com schedule' in chat.turn('List my bookings')
    assert not any(m['role'] == 'system' for m in chat.history)
    chat.turn('Another question')
    systems = [m for m in model.requests[-1] if m['role'] == 'system']
    assert len(systems) == 1
    assert 'last generation was incomplete' not in systems[0]['content']


def test_qwen_thinking_only_metadata_uses_supported_mode_and_larger_budget():
    requests=[]
    def handler(request):
        requests.append(request)
        if request.url.path=='/api/show':return httpx.Response(200,json={'thinking':{'values':[True]}})
        body=json.loads(request.content)
        assert body['think'] is True and body['options']['num_predict']==4096
        return httpx.Response(200,json={'done':True,'message':{'role':'assistant',
            'content':'Ready','thinking':'private reasoning'}})
    p=OllamaProvider('qwen3:30b-a3b',transport=httpx.MockTransport(handler))
    try:
        assert p.chat([],[]).content=='Ready'
        p.chat([],[])
    finally:p.close()
    assert sum(r.url.path=='/api/show' for r in requests)==1


def test_confirmation_without_pending_action_never_calls_model():
    model = Scripted()
    chat, _, executor = session(model)
    answer = chat.turn('confirm')
    assert 'no pending action' in answer
    assert not model.requests
    executor.execute.assert_not_called()


def test_precise_requested_time_availability_continues_to_move_proposal():
    model=Scripted(call('available_slots',number=1,event_type_id=1,
        start='2026-11-13T16:00:00',end='2026-11-13T16:30:00'),
        call('prepare_reschedule',number=1,start='2026-11-13T16:00:00'))
    chat, _, executor=session(model)
    chat.candidates=[BOOKING]
    answer=chat.turn('Move this meeting to 4 PM the same day')
    assert chat.pending and chat.pending.operation=='reschedule'
    assert "Reply 'confirm'" in answer
    executor.execute.assert_not_called()


def test_availability_infers_unique_template_match_and_returns_local_times():
    from dataclasses import replace
    chat, client, _=session(Scripted())
    chat.candidates=[replace(BOOKING,uid='other',event_type_id=2),BOOKING]
    result=chat._dispatch(ToolCall('available_slots',{'event_type_id':1,
        'start':'2026-11-13T12:00:00','end':'2026-11-13T17:00:00'}))
    assert result['rescheduling'] and result['offer_choices']
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule']==BOOKING.uid
    assert result['slots'][0]['start'].hour==12
    assert str(result['slots'][0]['start'].tzinfo)=='America/Los_Angeles'


def test_availability_does_not_infer_between_two_bookings_of_same_template():
    from dataclasses import replace
    chat, client, _=session(Scripted())
    chat.candidates=[BOOKING,replace(BOOKING,uid='other')]
    chat.events[EVENT.id]=EVENT
    result=chat._dispatch(ToolCall('available_slots',{'event_type_id':1,
        'start':'2026-11-13T12:00:00','end':'2026-11-13T17:00:00','duration':30}))
    assert result['rescheduling'] is False
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] is None


def test_kevin_name_survives_email_then_time_only_followups():
    model=Scripted(
        call('remember_booking_details',name='Kevin',date='2026-11-17',duration=30),
        ModelReply("Please provide Kevin's email address."),
        call('remember_booking_details',email='kevin@gmail.com'),
        call('list_event_types'),
        call('available_slots',event_type_id=1,start='2026-11-17T09:00:00',end='2026-11-17T17:00:00',duration=30),
        call('prepare_create',start='2026-11-17T13:00:00'))
    chat, _, executor=session(model)
    assert 'email' in chat.turn('book a meeting next Tue with Kevin')
    assert chat._booking_draft['name']=='Kevin'
    assert 'available slots' in chat.turn('kevin@gmail.com')
    assert not chat.pending
    answer=chat.turn('1:00 PM–1:30 PM')
    assert 'Kevin' in answer and "Reply 'confirm'" in answer
    assert not chat.diagnostics
    executor.execute.assert_not_called()
    assert 'Kevin' in model.requests[-1][0]['content']
    chat.turn('confirm')
    payload=executor.execute.call_args.args[1]
    assert payload['attendee']['name']=='Kevin'
    assert payload['attendee']['email']=='kevin@gmail.com'
    assert payload['start']=='2026-11-17T21:00:00Z'
    assert chat._booking_draft=={}


def test_booking_draft_new_attendee_does_not_reuse_previous_email():
    chat, _, _=session(Scripted())
    chat.history=[{'role':'user','content':'Book Kevin next Tuesday, kevin@gmail.com. Then book Alice on Wednesday.'}]
    chat._dispatch(ToolCall('remember_booking_details',{'name':'Kevin','email':'kevin@gmail.com','date':'2026-11-17'}))
    result=chat._dispatch(ToolCall('remember_booking_details',{'name':'Alice','date':'2026-11-18'}))
    assert chat._booking_draft=={'name':'Alice','date':'2026-11-18'}
    assert 'email' in result['missing']


def test_booking_draft_does_not_infer_name_from_email_and_reset_clears_it():
    from calcom_agent.scheduling import SchedulingError
    chat, _, _=session(Scripted())
    chat.history=[{'role':'user','content':'kevin@gmail.com'}]
    with pytest.raises(SchedulingError,match='missing_information'):
        chat._dispatch(ToolCall('remember_booking_details',{'name':'Kevin'}))
    chat.history.append({'role':'user','content':'The name is Kevin'})
    chat._dispatch(ToolCall('remember_booking_details',{'name':'Kevin','email':'kevin@gmail.com'}))
    chat.reset()
    assert chat._booking_draft=={}


def test_prepare_different_attendee_cannot_reuse_draft_email():
    from calcom_agent.scheduling import SchedulingError
    chat, _, _=session(Scripted())
    chat.history=[{'role':'user','content':'Kevin kevin@gmail.com. Actually book Alice instead.'}]
    chat.events[EVENT.id]=EVENT
    chat._dispatch(ToolCall('remember_booking_details',{'name':'Kevin','email':'kevin@gmail.com','date':'2026-11-17'}))
    with pytest.raises(SchedulingError,match='missing_information') as error:
        chat._dispatch(ToolCall('prepare_create',{'name':'Alice','event_type_id':1,'start':'2026-11-17T13:00:00'}))
    assert error.value.fields==('email',)
    assert 'email' not in chat._booking_draft


def test_matt_thursday_and_time_survive_email_reply_and_reject_monday_drift():
    model=Scripted(call('remember_booking_details',name='Matt',date='2026-11-19',weekday=3,time='10:30'),
        ModelReply("What is Matt's email?"),call('list_event_types'),
        call('prepare_create',start='2026-11-16T10:30:00',email='matt@gmail.com'),
        call('prepare_create',start='2026-11-19T10:30:00',email='matt@gmail.com'))
    chat, _, executor=session(model)
    chat.turn('setup a 1:1 with Matt on next Thu 10:30')
    answer=chat.turn('matt@gmail.com')
    assert '2026-11-19T10:30:00-08:00' in answer
    assert len(model.requests)==2  # Email completion does not need another model response.
    executor.execute.assert_not_called()
    chat.turn('confirm')
    assert executor.execute.call_args.args[1]['start']=='2026-11-19T18:30:00Z'


def test_monday_date_cannot_be_saved_as_requested_thursday():
    from calcom_agent.scheduling import SchedulingError
    chat, _, _=session(Scripted())
    chat.history=[{'role':'user','content':'Matt next Thu at 10:30'}]
    with pytest.raises(SchedulingError,match='booking_date_weekday_mismatch'):
        chat._dispatch(ToolCall('remember_booking_details',{'name':'Matt','date':'2026-11-16','weekday':3,'time':'10:30'}))
    assert not chat._booking_draft and not chat.pending


def test_exact_matt_time_cannot_turn_into_alternative_availability_options():
    chat, client, executor=session(Scripted(),allow_conflicts=True)
    chat.history=[{'role':'user','content':'Matt next Thu at 10:30, matt@gmail.com'}]
    chat.events[EVENT.id]=EVENT
    chat._dispatch(ToolCall('remember_booking_details',{'name':'Matt','email':'matt@gmail.com',
        'date':'2026-11-19','weekday':3,'time':'10:30'}))
    client.available_slots.side_effect=lambda *a,**kw: []
    result=chat._dispatch(ToolCall('available_slots',{'event_type_id':1,
        'start':'2026-11-19T10:00:00','end':'2026-11-19T10:30:00','duration':30}))
    assert result['confirmation_required']
    assert '2026-11-19T10:30:00-08:00' in chat.pending.summary
    assert 'slots' not in result
    client.available_slots.assert_not_called()
    executor.execute.assert_not_called()


def test_default_overlap_policy_books_then_warns_without_moving():
    model=Scripted(call('remember_booking_details',name='Matt',date='2026-11-19',weekday=3,time='10:30'),
                   ModelReply('What email?'))
    chat, client, executor=session(model,allow_conflicts=True)
    chat.turn('Matt next Thu 10:30')
    answer=chat.turn('matt@gmail.com')
    assert '10:30' in answer and chat.pending
    executor.execute.assert_not_called()
    executor.execute.return_value={'status':'accepted','uid':'created',
        'verification':{'calcom':'verified','google_calendar':{'status':'verified'}},
        'conflicts':{'status':'detected'}}
    answer=chat.turn('confirm')
    assert 'has a time conflict' in answer and 'suggest a different time' in answer
    assert executor.execute.call_args.args[1]['allowConflicts'] is True
    assert executor.execute.call_args.args[1]['start']=='2026-11-19T18:30:00Z'
    assert executor.execute.call_count==1 and not chat.pending

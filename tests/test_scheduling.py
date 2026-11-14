from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from calcom_agent.client import Booking, EventType, Slot, CalcomError
from calcom_agent.scheduling import SchedulingError, SchedulingService, local_time, resolve_booking

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
START = NOW + timedelta(days=1)
EVENT = EventType(1, 'Demo', 30, [], [])
BOOKING = Booking('demo-uid', 'Demo', 'accepted', START, START + timedelta(minutes=30), 1)


@pytest.fixture
def setup():
    client = Mock()
    client.get_event_type.return_value = EVENT
    client.list_bookings.return_value = [BOOKING]
    client.available_slots.side_effect = lambda _, start, end, **kw: [Slot(start, end)]
    clock = Mock(return_value=NOW)
    return SchedulingService(client, clock=clock), client, clock, Mock()


def create(service, **kwargs):
    return service.prepare_create(1, START, name='Demo', email='demo@example.test',
                                  time_zone='America/Los_Angeles', **kwargs)


def test_create_confirmation_payload_snapshot_and_single_execution(setup):
    service, client, _, executor = setup
    proposal = create(service)
    assert '2026-11-13T04:00:00-08:00' in proposal.summary
    with pytest.raises(SchedulingError, match='confirmation_required'):
        service.execute_confirmed(proposal.token, confirmed=False, executor=executor)
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed('wrong', confirmed=True, executor=executor)
    executor.execute.assert_not_called()
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    operation, payload = executor.execute.call_args.args
    assert operation == 'create'
    assert payload['start'] == '2026-11-13T12:00:00Z'
    assert payload['attendee']['email'] == 'demo@example.test'
    assert client.available_slots.call_count == 2
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_count == 1


def test_cancel_reschedule_payloads_and_original_booking_exclusion(setup):
    service, client, _, executor = setup
    proposal = service.prepare_cancel('demo-uid', reason='Demo reason')
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_args.args == ('cancel', {'bookingUid': 'demo-uid', 'cancellationReason': 'Demo reason'})
    proposal = service.prepare_reschedule('demo-uid', START + timedelta(hours=2))
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_args.args[0] == 'reschedule'
    assert executor.execute.call_args.args[1]['start'] == '2026-11-13T14:00:00Z'
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] == 'demo-uid'


def test_missing_fields_invalid_email_and_edit_revoke_confirmation(setup):
    service, _, _, executor = setup
    proposal = create(service)
    with pytest.raises(SchedulingError) as exc:
        service.prepare_create(1, START)
    assert exc.value.fields == ('name', 'email', 'timeZone')
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    with pytest.raises(SchedulingError, match='invalid_email'):
        service.prepare_create(1, START, name='Demo', email='invalid', time_zone='UTC')
    executor.execute.assert_not_called()


def test_required_questions_and_unsupported_duration(setup):
    service, client, _, _ = setup
    client.get_event_type.return_value = replace(EVENT, booking_fields=[{'slug': 'company', 'required': True}])
    with pytest.raises(SchedulingError) as exc:
        create(service)
    assert exc.value.fields == ('company',)
    answers = {'company': 'Demo'}
    proposal = create(service, answers=answers)
    answers['company'] = 'changed'
    executor = Mock()
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_args.args[1]['bookingFieldsResponses'] == {'company': 'Demo'}
    with pytest.raises(SchedulingError, match='unsupported_duration'):
        create(service, answers={'company': 'Demo'}, duration=45)
    client.get_event_type.return_value = replace(EVENT, policy={'lengthInMinutesOptions': [15, 30]})
    proposal = create(service, duration=15)
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_args.args[1]['lengthInMinutes'] == 15


def test_reference_resolution_requires_unique_real_candidate():
    duplicate = replace(BOOKING, uid='other')
    with pytest.raises(SchedulingError, match='ambiguous_booking'):
        resolve_booking([BOOKING, duplicate], title='demo')
    assert resolve_booking([BOOKING, duplicate], number=2) == duplicate
    assert resolve_booking([BOOKING], uid='demo-uid') == BOOKING
    with pytest.raises(SchedulingError, match='booking_not_found'):
        resolve_booking([BOOKING], uid='invented')
    with pytest.raises(SchedulingError, match='choose_one'):
        resolve_booking([BOOKING], uid='demo-uid', number=1)


def test_dst_gaps_folds_and_timezone_date_boundary():
    with pytest.raises(SchedulingError, match='nonexistent'):
        local_time(datetime(2026, 3, 8, 2, 30), 'America/Los_Angeles')
    repeated = datetime(2026, 11, 1, 1, 30)
    with pytest.raises(SchedulingError, match='ambiguous'):
        local_time(repeated, 'America/Los_Angeles')
    assert local_time(repeated, 'America/Los_Angeles', fold=1) - local_time(repeated, 'America/Los_Angeles', fold=0) == timedelta(hours=1)
    assert local_time(datetime(2026, 11, 12, 23), 'America/Los_Angeles') == datetime(2026, 11, 13, 7, tzinfo=timezone.utc)


@pytest.mark.parametrize('change', ['booking', 'event', 'expired', 'slot', 'api'])
def test_stale_expired_unavailable_or_failed_refresh_never_executes(setup, change):
    service, client, clock, executor = setup
    proposal = service.prepare_reschedule('demo-uid', START + timedelta(hours=1))
    if change == 'booking':
        client.list_bookings.return_value = [replace(BOOKING, title='Changed')]
    elif change == 'event':
        client.get_event_type.return_value = replace(EVENT, title='Changed')
    elif change == 'expired':
        clock.return_value = NOW + timedelta(minutes=5)
    elif change == 'slot':
        client.available_slots.side_effect = None
        client.available_slots.return_value = []
    else:
        client.get_event_type.side_effect = CalcomError('timeout')
    with pytest.raises((SchedulingError, CalcomError)):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    executor.execute.assert_not_called()
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)


def test_executor_failure_is_not_retried(setup):
    service, _, _, executor = setup
    proposal = create(service)
    executor.execute.side_effect = CalcomError('timeout')
    with pytest.raises(CalcomError):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_count == 1


def test_unsupported_policies_inactive_booking_and_past_time(setup):
    service, client, _, executor = setup
    for policy in [{'price': 10}, {'recurrence': {'disabled': False}}, {'seatsPerTimeSlot': 2}]:
        client.get_event_type.return_value = replace(EVENT, policy=policy)
        with pytest.raises(SchedulingError, match='unsupported_event_type'):
            create(service)
    client.get_event_type.return_value = EVENT
    client.list_bookings.return_value = [replace(BOOKING, status='cancelled')]
    with pytest.raises(SchedulingError, match='booking_not_active'):
        service.prepare_cancel('demo-uid')
    with pytest.raises(SchedulingError, match='future'):
        service.prepare_create(1, NOW, name='Demo', email='demo@example.test', time_zone='UTC')
    executor.execute.assert_not_called()


def test_service_with_real_read_client_and_mock_http():
    import httpx
    from calcom_agent.client import CalcomClient
    from calcom_agent.config import Settings
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        if request.url.path == '/v2/event-types/1':
            data = {'id': 1, 'title': 'Demo', 'lengthInMinutes': 30,
                    'lengthInMinutesOptions': [15, 30], 'bookingFields': [], 'locations': []}
        else:
            assert request.url.path == '/v2/slots'
            assert request.url.params['duration'] == '15'
            data = {'2026-11-13': [{'start': '2026-11-13T12:00:00Z', 'end': '2026-11-13T12:15:00Z'}]}
        return httpx.Response(200, json={'status': 'success', 'data': data})
    with CalcomClient(Settings('synthetic-key', 'demo'), transport=httpx.MockTransport(handler)) as api:
        service = SchedulingService(api, clock=lambda: NOW)
        proposal = create(service, duration=15)
        executor = Mock()
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
        assert executor.execute.call_args.args[1]['lengthInMinutes'] == 15
    assert len(requests) == 4


def test_question_types_location_choices_and_disabled_operations(setup):
    service, client, _, executor = setup
    client.get_event_type.return_value = replace(EVENT, booking_fields=[{'slug': 'company', 'type': 'text', 'required': True}])
    for answer in ('   ', 123):
        with pytest.raises(SchedulingError):
            create(service, answers={'company': answer})
    client.get_event_type.return_value = replace(EVENT, locations=[{'type': 'link'}, {'type': 'address'}])
    with pytest.raises(SchedulingError, match='location_choice_required'):
        create(service)
    client.get_event_type.return_value = replace(EVENT, policy={'disableCancelling': {'disabled': True}})
    with pytest.raises(SchedulingError, match='cancellation_disabled'):
        service.prepare_cancel('demo-uid')
    client.get_event_type.return_value = replace(EVENT, policy={'disableRescheduling': {'disabled': True}})
    with pytest.raises(SchedulingError, match='rescheduling_disabled'):
        service.prepare_reschedule('demo-uid', START + timedelta(hours=1))
    executor.execute.assert_not_called()


def test_duration_update_preserves_start_and_rechecks_new_length(setup):
    service, client, _, executor = setup
    client.get_event_type.return_value = replace(EVENT, policy={'lengthInMinutesOptions': [30, 60]})
    proposal = service.prepare_update(BOOKING.uid, duration=60)
    assert 'duration 60 minutes' in proposal.summary
    executor.execute.assert_not_called()
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert executor.execute.call_args.args == ('reschedule', {
        'bookingUid': BOOKING.uid, 'start': START.isoformat().replace('+00:00', 'Z'),
        'lengthInMinutes': 60, 'reschedulingReason': ''})
    assert client.available_slots.call_count == 2
    assert client.available_slots.call_args.args[2] == START + timedelta(hours=1)
    assert client.available_slots.call_args.kwargs['booking_uid_to_reschedule'] == BOOKING.uid


@pytest.mark.parametrize('duration', [0, -1, True, 1.5, 60])
def test_invalid_or_unsupported_duration_update_revokes_old_proposal(setup, duration):
    service, _, _, executor = setup
    old = service.prepare_cancel(BOOKING.uid)
    with pytest.raises(SchedulingError, match='unsupported_duration'):
        service.prepare_update(BOOKING.uid, duration=duration)
    with pytest.raises(SchedulingError, match='invalid_confirmation'):
        service.execute_confirmed(old.token, confirmed=True, executor=executor)
    executor.execute.assert_not_called()


def test_duration_change_conflict_on_confirmation_never_writes(setup):
    service, client, _, executor = setup
    client.get_event_type.return_value = replace(EVENT, policy={'lengthInMinutesOptions': [30, 60]})
    proposal = service.prepare_update(BOOKING.uid, duration=60)
    client.available_slots.side_effect = lambda *a, **kw: []
    with pytest.raises(SchedulingError, match='slot_unavailable'):
        service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    executor.execute.assert_not_called()


def test_reschedule_fixed_duration_omits_multi_duration_only_api_field(setup):
    service, _, _, executor = setup
    proposal = service.prepare_reschedule(BOOKING.uid, START + timedelta(hours=2), duration=30)
    service.execute_confirmed(proposal.token, confirmed=True, executor=executor)
    assert 'lengthInMinutes' not in executor.execute.call_args.args[1]


def test_static_seminar_title_can_use_its_configured_template(setup):
    service, client, _, executor=setup
    client.get_event_type.return_value=replace(EVENT, title='Acceptance Seminar',
        booking_fields=[{'slug':'title','type':'text','required':True}],
        policy={'customName':'Acceptance Seminar'})
    proposal=create(service)
    service.execute_confirmed(proposal.token,confirmed=True,executor=executor)
    assert executor.execute.call_args.args[1]['bookingFieldsResponses']['title']=='Acceptance Seminar'


def test_cancel_without_reason_previews_truthful_nonempty_request_reason(setup):
    service, _, _, executor=setup
    p=service.prepare_cancel(BOOKING.uid)
    assert 'User requested cancellation' in p.summary
    service.execute_confirmed(p.token,confirmed=True,executor=executor)
    assert executor.execute.call_args.args[1]['cancellationReason']=='User requested cancellation'


@pytest.mark.parametrize('operation',['create','reschedule'])
def test_explicit_overlap_permission_is_previewed_and_confirmed(setup,operation):
    service, client, _, executor=setup
    client.available_slots.side_effect=lambda *a,**kw: []
    if operation=='create':proposal=create(service,allow_conflicts=True)
    else:proposal=service.prepare_reschedule(BOOKING.uid,START+timedelta(hours=2),allow_conflicts=True)
    assert 'Allow overlapping meetings' in proposal.summary
    executor.execute.assert_not_called()
    with pytest.raises(SchedulingError,match='confirmation_required'):
        service.execute_confirmed(proposal.token,confirmed=False,executor=executor)
    service.execute_confirmed(proposal.token,confirmed=True,executor=executor)
    payload=executor.execute.call_args.args[1]
    assert payload['allowConflicts'] is True
    assert 'allowBookingOutOfBounds' not in payload and 'skipBookingLimits' not in payload
    client.available_slots.assert_not_called()

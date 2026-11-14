"""Bounded weekly schedules, represented as individually verified bookings."""
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import re

from .client import CalcomError, utc_string
from .scheduling import SchedulingError, local_time


def validate_patch(args):
    if set(args) - {'title','weekdays','time','email','weeks','until'}:
        raise SchedulingError('invalid_tool_arguments')
    if 'weeks' in args and 'until' in args:
        raise SchedulingError('choose_series_end')
    if 'weekdays' in args and (not args['weekdays'] or any(type(d) is not int or not 0 <= d <= 6 for d in args['weekdays']) or len(set(args['weekdays'])) != len(args['weekdays'])):
        raise SchedulingError('invalid_tool_arguments', ('weekdays',))
    if 'time' in args and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', args['time']):
        raise SchedulingError('invalid_tool_arguments', ('time',))
    if 'until' in args:
        try: date.fromisoformat(args['until'])
        except (TypeError, ValueError): raise SchedulingError('invalid_tool_arguments', ('until',)) from None
    for key in ('title','email'):
        if key in args and (not isinstance(args[key],str) or not args[key].strip()):
            raise SchedulingError('missing_information', (key,))
    if 'email' in args and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args['email']):
        raise SchedulingError('invalid_email', ('email',))
    if 'weeks' in args and type(args['weeks']) is not int:
        raise SchedulingError('invalid_tool_arguments', ('weeks',))


def prepare(service, draft, zone):
    if not draft['email']: return 'What email address should I invite to ' + draft['title'] + '?'
    if not draft['weekdays'] or not draft['time']: return 'Which weekdays and local time should the weekly meeting use?'
    now = service._clock().astimezone(ZoneInfo(zone))
    until = None
    ending = draft.get('until')
    if ending:
        try:
            until = date.fromisoformat(ending)
        except (ValueError, TypeError):
            pass
        if until is None:
            return 'I couldn’t resolve that end date. Please use a date such as 2026-12-31, “until the end of this year”, or a number of weeks.'
        if until < now.date():
            return 'That end date is in the past. Please choose a future end date.'
        if (until - now.date()).days > 112:
            return f'I understood the end date as {until}, but this app supports at most 16 weeks and 32 occurrences per confirmed schedule. Please choose a nearer end date.'
    elif draft['weeks'] is None:
        return 'For how many weeks should this repeat, or on what date should it end? I have ' + draft['title'] + ' at ' + draft['time'] + ' on ' + ', '.join(('Mon','Tue','Wed','Thu','Fri','Sat','Sun')[d] for d in sorted(draft['weekdays'])) + f' ({zone}), 30 minutes. The team address receives the invitation; individual member delivery depends on the mailing list.'
    elif not 1 <= draft['weeks'] <= 16:
        return 'Please choose 1–16 weeks (at most 32 occurrences).'
    day = now.date()
    dates = []
    target = draft['weeks'] * len(draft['weekdays']) if until is None else None
    if target and target > 32:
        return 'Please shorten the series to at most 32 occurrences.'
    while (day <= until if until is not None else len(dates) < target):
        if day.weekday() in draft['weekdays']:
            start = local_time(datetime.fromisoformat(f"{day}T{draft['time']}"), zone)
            if start > now: dates.append(start)
        day += timedelta(days=1)
    if not dates:
        return 'There are no future occurrences on the selected weekdays before that end date. Please choose a later end date.'
    if len(dates) > 32:
        return f'That end date produces {len(dates)} occurrences; the limit is 32. Please choose a nearer end date.'
    period = f'through {until} inclusive' if until else f"for {draft['weeks']} weeks"
    events = service._client.list_event_types()
    event = next((e for e in events if e.length_in_minutes == 30 and service._standard(e)), None)
    if event is None: raise SchedulingError('discover_event_type_first')
    for start in dates: service._slot(event, start, 30)
    payload = {**draft, 'starts': [utc_string(d) for d in dates], 'zone': zone}
    weekdays = ', '.join(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[d] for d in sorted(draft['weekdays']))
    wall = datetime.strptime(draft['time'], '%H:%M').strftime('%I:%M %p').lstrip('0')
    summary = (f"I will create {draft['title']} meetings every {weekdays} at {wall} ({zone}), "
               f"{period}, 30 minutes each, and invite {draft['email']}. "
               f"There are {len(dates)} individual bookings, starting {dates[0].astimezone(ZoneInfo(zone)).date()}. "
               "A dedicated meeting template will be created if needed. "
               "These are individual occurrences rather than a native recurring-calendar event. "
               "Each booking will be verified; a failure stops the remaining writes.\n\nDates:\n" +
               '\n'.join(d.astimezone(ZoneInfo(zone)).isoformat() for d in dates))
    return service._save('create_series', payload, None, None, summary)


def execute(client, payload, *, executor=None):
    writer = executor if executor is not None else client
    results = []
    event_id = None
    failed_start = None
    phase = 'template_discovery'
    template_created = False
    attempted = 0
    try:
        events = client.list_event_types()
        event = next((e for e in events if e.title == payload['title'] and e.policy.get('customName') == payload['title'] and e.length_in_minutes == 30), None)
        if event is None:
            phase = 'template_creation'
            result = writer.execute('create_event_type', {'title': payload['title'], 'slug': 'team-series-' + uuid4().hex[:12],
                'lengthInMinutes': 30, 'locations': [{'type':'integration','integration':'cal-video'}], 'customName': payload['title']})
            event_id = result['eventType']['id']
            template_created = True
        else: event_id = event.id
        from .scheduling import SchedulingService
        from .client import parse_time
        service = SchedulingService(client)
        phase = 'availability_validation'
        event = service._event(event_id)
        # Validate all occurrences against the actual destination template before any booking write.
        for start in payload['starts']:
            failed_start = start
            service._slot(event, parse_time(start), 30)
        for start in payload['starts']:
            failed_start = start
            phase = 'booking_creation'
            attempted += 1
            result = writer.execute('create', {'eventTypeId':event_id, 'start': start,
                'attendee': {'name':payload['title'], 'email':payload['email'], 'timeZone':payload['zone']},
                'bookingFieldsResponses': {'title':payload['title']}})
            results.append(result)
        return {'series':results, 'complete':True}
    except (CalcomError, SchedulingError) as error:
        return {'series':results, 'complete':False, 'error':error.code, 'unverified_uid':getattr(error,'booking_uid',None),
                'http_status':getattr(error,'status_code',None), 'template_id':event_id, 'failed_start':failed_start,
                'request_rejected':isinstance(error,CalcomError) and getattr(error,'status_code',None)==400,
                'phase':phase, 'attempted':attempted, 'total':len(payload['starts']), 'title':payload['title'],
                'email':payload['email'], 'zone':payload['zone'], 'template_created':template_created,
                'provider_details':getattr(error,'details',{})}

"""Explicit live acceptance runner. Requires --execute; only mutates its own fixtures.

Uses the actual configured LLM and Cal.com. May send test invitations to the
account owner's mailbox. Reports redact that address. No automatic POST retries.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4

from calcom_agent.client import CalcomClient, CalcomError, parse_time, utc_string, ONE_ON_ONE_NAME
from calcom_agent.config import load_settings
from calcom_agent.conversation import Conversation
from calcom_agent.ollama import OllamaProvider
from calcom_agent.scheduling import SchedulingService
from calcom_agent.ui_session import ChatSession

REPORT = Path('reports/acceptance.json')
STATE = Path('reports/acceptance.state.json')


class Run:
    def __init__(self):
        self.settings = load_settings()
        self.client = CalcomClient(self.settings)
        self._last_request = 0.0
        self.client._http.event_hooks['request'].append(self.throttle)
        self.provider = OllamaProvider(self.settings.ollama_model, self.settings.ollama_base_url,
                                       timeout_seconds=self.settings.ollama_timeout_seconds)
        self.zone = ZoneInfo(self.settings.default_timezone)
        self.tag = 'CalendarTest' + uuid4().hex[:6]
        self.rows, self.owned, self.templates, self.writes, self.google = [], set(), [], [], {}
        self.unknown = False
        self.only = set()
        self.used_starts = set()
        self.email = self.client._get('me', self.client.ME_VERSION)['data']['email']
        calendars = self.client._get('calendars', self.client.ME_VERSION)['data']
        self.destination = calendars['destinationCalendar']
        assert self.destination['name'].casefold() == 'scheduling demo', 'Wrong destination'
        self.event = next(e for e in self.client.list_event_types()
                          if e.policy.get('customName') == ONE_ON_ONE_NAME and e.length_in_minutes == 30)
        now = datetime.now(self.zone)
        self.monday = now.date() + timedelta(days=7-now.weekday())
        self.day = datetime.combine(self.monday, datetime.min.time(), self.zone)
        self.slots = self.client.available_slots(self.event.id, self.day,
            self.day+timedelta(days=1), duration=30, time_zone=str(self.zone))
        morning = [s for s in self.slots if 10 <= s.start.astimezone(self.zone).hour < 12]
        assert morning, 'No free Monday morning slot'
        self.start = morning[0].start
        self.chat = self.new_chat()
        self.save()

    def throttle(self, request):
        delay=0.7-(time.monotonic()-self._last_request)
        if delay>0: time.sleep(delay)
        self._last_request=time.monotonic()

    def save(self):
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps({'run': self.tag, 'model': self.settings.ollama_model,
            'timezone': str(self.zone), 'test_date': self.monday.isoformat(),
            'destination': self.destination['name'], 'templates_created': self.templates, 'cases': self.rows,
            'writes': self.writes, 'google_checks': self.google}, indent=2, default=str))
        REPORT.chmod(0o600)
        STATE.write_text(json.dumps({'run': self.tag, 'owned': sorted(self.owned),
            'templates': self.templates, 'report': str(REPORT)}))
        STATE.chmod(0o600)

    def new_chat(self):
        return ChatSession(Conversation(self.provider, self.client, executor=self,
                           time_zone=str(self.zone)))

    def execute(self, operation, payload):
        if operation not in {'create', 'reschedule', 'cancel', 'update'}:
            raise ValueError('Test executor refuses template writes')
        if operation != 'create' and payload['bookingUid'] not in self.owned:
            raise ValueError('Test executor refuses unrelated booking writes')
        self.writes.append({'operation': operation, 'outcome': 'attempted'})
        self.save()
        try:
            result = self.client.execute(operation, payload)
        except CalcomError as e:
            if getattr(e, 'booking_uid', None): self.owned.add(e.booking_uid)
            self.writes[-1]['outcome'] = e.code
            if e.code == 'write_outcome_unknown': self.unknown = True
            self.save()
            raise
        self.owned.add(result['uid'])
        self.writes[-1].update(outcome='verified', uid=result['uid'],
                              verification=result.get('verification'))
        self.save()
        self.check_google(result['uid'])
        return result

    def check_google(self, uid):
        try:
            raw = self.client._get(self.client._uid_path(uid), self.client.WRITE_VERSION)['data']
            refs = self.client._google_references(uid)
            check = self.client.verify_google_calendar(raw, cancelled=raw['status']=='cancelled', references=refs)
            google_refs=[r for r in refs if r.get('type')=='google_calendar']
            if google_refs and any(r.get('destinationCalendarId')!=self.destination['externalId'] for r in google_refs):
                check={'status':'unverified','reason':'wrong_destination_calendar'}
            busy = self.google_busy(raw['start'], raw['end'])
            prior = self.google.get(uid, {})
            was_observed = prior.get('observed_busy', False) or (raw['status'] in {'accepted','pending'} and busy is True)
            if raw['status']=='cancelled' and check['status']!='verified' and was_observed and busy is False:
                check = {'status':'verified', 'method':'google_calendar_busy_interval_cleared'}
            self.google[uid] = {'observed_busy': was_observed, 'busy_now': busy, 'calcom_status': raw['status'], 'title': raw['title'],
                'start': raw['start'], 'end': raw['end'], 'check': check,
                'destination_matches_default': all(r.get('destinationCalendarId') == self.destination['externalId']
                    for r in refs if r.get('type') == 'google_calendar') if refs else None}
            self.save()
            return check
        except CalcomError as e:
            return {'status': 'unverified', 'reason': e.code}

    def google_busy(self, start, end):
        start, end = parse_time(start), parse_time(end)
        date_from=start.astimezone(self.zone).date()
        date_to=end.astimezone(self.zone).date()+timedelta(days=1)
        try:
            rows=self.client._get('calendars/busy-times',self.client.ME_VERSION,{
                'dateFrom':date_from.isoformat(), 'dateTo':date_to.isoformat(),
                'timeZone':str(self.zone), 'calendar':str(self.destination['credentialId'])+','+self.destination['externalId']})['data']
            return any(parse_time(r['start'])<end and parse_time(r['end'])>start for r in rows)
        except (CalcomError, ValueError, KeyError, TypeError):
            return None

    def tool_trace(self, chat):
        trace=[]
        for message in chat.conversation.history:
            if message['role']!='tool': continue
            data=json.loads(message['content'])
            entry={'name':message.get('tool_name')}
            for key in ('error','start','end','time_zone','confirmation_required'):
                if key in data:entry[key]=data[key]
            for key in ('bookings','slots','event_types'):
                if key in data:entry[key+'_count']=len(data[key])
            trace.append(entry)
        return trace[-8:]

    def turn(self, prompt, chat=None):
        chat = chat or self.chat
        assert chat.enqueue(prompt)
        chat.process()
        message = chat.messages[-1]
        return {'prompt': prompt.replace(self.email, '[account-owner email]'),
            'answer': message['content'].replace(self.email, '[account-owner email]'),
            'diagnostics': [{k:d[k] for k in ('stage','code')} for d in message['diagnostics']],
            'model_drafts': len(message.get('model_outputs', [])),
            'tool_trace': self.tool_trace(chat),
            'pending': chat.conversation.pending.operation if chat.conversation.pending else None}

    def case(self, ident, action):
        row = {'id': ident, 'status': 'Running', 'turns': []}
        self.rows.append(row); self.current = row; self.save()
        print('RUN', ident, flush=True)
        try:
            action(row)
            row['status'] = 'Pass'
        except Exception as e:
            row['status'] = 'Fail'
            row['reason'] = (getattr(e, 'code', None) or (str(e) if isinstance(e, AssertionError) else type(e).__name__))
        self.save()
        print(row['status'].upper(), ident, row.get('reason',''), flush=True)
        if self.unknown:
            raise SystemExit('Unknown write outcome: stopped without retry; inspect the run marker in Cal.com and Google.')

    def ask(self, row, prompt, chat=None):
        turn = self.turn(prompt, chat)
        row['turns'].append(turn); self.save()
        return turn

    def active(self):
        return [b for b in self.client.list_bookings() if b.uid in self.owned and b.status in {'accepted','pending'}]

    def fixture(self, start=None, event=None):
        event=event or self.event
        if start is None:
            slots=self.client.available_slots(event.id,self.day+timedelta(hours=10),
                self.day+timedelta(hours=17),duration=30,time_zone=str(self.zone))
            available={s.start for s in slots}
            candidates=[s for s in slots if s.start not in self.used_starts
                        and s.start+timedelta(minutes=30) in available]
            assert candidates, 'No fresh available fixture interval'
            start=candidates[0].start
            self.start=start
        self.used_starts.add(start)
        service = SchedulingService(self.client)
        proposal = service.prepare_create(event.id, start,
            name=self.tag, email=self.email, time_zone=str(self.zone))
        return service.execute_confirmed(proposal.token, confirmed=True, executor=self)

    def cleanup(self):
        for b in self.active():
            try:
                service = SchedulingService(self.client)
                p = service.prepare_cancel(b.uid, reason=self.tag+' cleanup')
                service.execute_confirmed(p.token, confirmed=True, executor=self)
                print('CLEANED test booking', flush=True)
            except Exception as e:
                print('CLEANUP FAILED', getattr(e,'code',type(e).__name__),flush=True)
        if not self.active():
            for eid in self.templates:
                try:
                    self.client._request('DELETE','event-types/'+str(eid),self.client.EVENT_VERSION)
                    print('CLEANED test template',flush=True)
                except CalcomError as e:print('TEMPLATE CLEANUP FAILED',e.code,flush=True)
        self.save()
        self.provider.close(); self.client.close()

    def core(self):
        date = self.monday.isoformat()
        time = self.start.astimezone(self.zone).strftime('%I:%M %p')
        def c01(row):
            before=len(self.writes)
            t=self.ask(row,f'Book a 30-minute meeting with {self.tag} on {date} at {time}. Their email is {self.email}.')
            assert t['pending']=='create', 'No creation proposal'
            assert len(self.writes)==before, 'Wrote before confirmation'
        self.case('C01',c01)
        def c02(row):
            assert self.chat.conversation.pending, 'C01 did not prepare a booking'
            t=self.ask(row,'confirm')
            assert self.active(), 'No active test booking'
            b=self.active()[0]
            assert self.check_google(b.uid)['status']=='verified', 'Google creation unverified'
        self.case('C02',c02)
        # A failed LLM creation must not prevent checking independent follow-ups.
        if not self.active():
            self.fixture()
            self.chat=self.new_chat()
        def listing(row):
            t=self.ask(row,f"What's my schedule on {date}?")
            ids={b.uid for b in self.active()}
            assert ids.issubset({b.uid for b in self.chat.conversation.candidates}), 'Test booking absent from listing'
            assert not t['diagnostics'], 'Listing has diagnostics'
            assert 'Your Cal.com schedule' in t['answer'], 'No readable schedule'
        self.case('C03',listing)
        def c04(row):
            before=len(self.writes)
            t=self.ask(row,f'Can we reschedule the {self.tag} meeting to afternoon on the same day?')
            assert 'available slots' in t['answer'], 'No actual afternoon options'
            assert not t['pending'], 'Selected a time without asking'
            assert len(self.writes)==before, 'Moved before confirmation'
        self.case('C04',c04)
        b=self.active()[0]
        afternoon=self.client.available_slots(b.event_type_id,self.day+timedelta(hours=12),
            self.day+timedelta(hours=17),duration=30,time_zone=str(self.zone),booking_uid_to_reschedule=b.uid)
        assert afternoon, 'No afternoon availability'
        self.target=afternoon[0].start
        def c05(row):
            before=len(self.writes)
            t=self.ask(row,'Move the '+self.tag+' meeting to '+self.target.astimezone(self.zone).strftime('%I:%M %p')+' on '+date+'.')
            assert t['pending']=='reschedule', 'No move proposal'
            assert len(self.writes)==before, 'Moved before confirmation'
        self.case('C05',c05)
        def c06(row):
            assert self.chat.conversation.pending, 'C05 did not prepare a move'
            self.ask(row,'confirm')
            assert any(b.start==self.target for b in self.active()), 'Start did not move'
            assert all(self.check_google(b.uid)['status']=='verified' for b in self.active()), 'Google move unverified'
        self.case('C06',c06)
        self.case('C07',listing)
        def c08(row):
            before=len(self.writes)
            t=self.ask(row,'Cancel the '+self.tag+' meeting.')
            assert t['pending']=='cancel', 'No cancellation proposal'
            assert len(self.writes)==before, 'Cancelled before confirmation'
        self.case('C08',c08)
        def c09(row):
            assert self.chat.conversation.pending, 'C08 did not prepare cancellation'
            self.ask(row,'confirm')
            assert not self.active(), 'Test booking still active in Cal.com'
            checks=[self.check_google(uid) for uid in self.owned]
            assert all(x['status']=='verified' for x in checks), 'Cal.com cancelled; independent Google cancellation not verified'
        self.case('C09',c09)
        def c10(row):
            self.ask(row,f"What's my schedule on {date}?")
            assert not ({b.uid for b in self.chat.conversation.candidates}&self.owned), 'Cancelled test booking still listed'
            assert not self.active(), 'Test fixture still active'
        self.case('C10',c10)


    def cancel_fixtures(self):
        for b in self.active():
            service = SchedulingService(self.client)
            p = service.prepare_cancel(b.uid, reason=self.tag+' fixture cleanup')
            service.execute_confirmed(p.token, confirmed=True, executor=self)

    def select_fixture(self, row, uid, chat=None):
        chat = chat or self.chat
        self.ask(row, f"What's my schedule on {self.monday.isoformat()}?", chat)
        candidates=chat.conversation.candidates
        assert any(b.uid==uid for b in candidates), 'Fixture not listed'
        return next(i for i,b in enumerate(candidates,1) if b.uid==uid)

    def move_proposal(self, row, uid, target, chat=None):
        chat=chat or self.chat
        number=self.select_fixture(row,uid,chat)
        label=target.astimezone(self.zone).strftime('%I:%M %p')
        t=self.ask(row,f'Move meeting number {number} to {label} on {self.monday.isoformat()}.',chat)
        assert t['pending']=='reschedule', 'No reschedule proposal'
        return t

    def regressions(self):
        date=self.monday.isoformat()
        multi=None
        if not self.only or self.only & {'R03','R04'}:
            try:
                raw=self.client._request('POST','event-types',self.client.EVENT_VERSION,body={
                    'title': self.tag+' Seminar', 'slug':self.tag.lower()+'-seminar',
                    'lengthInMinutes':30, 'lengthInMinutesOptions':[30,60],
                    'customName': self.tag+' Seminar',
                    'locations':[{'type':'integration','integration':'cal-video'}]})['data']
                self.templates.append(raw['id']); self.save()
                multi=self.client.get_event_type(raw['id'])
                assert multi.policy.get('lengthInMinutesOptions') == [30,60], 'Template options missing'
                print('READY dedicated multi-duration seminar template',flush=True)
            except Exception as e:
                print('Multi-duration prerequisite failed:',getattr(e,'code',type(e).__name__),flush=True)
        def execute_case(ident, action):
            if self.only and ident not in self.only: return
            self.chat=self.new_chat()
            self.case(ident,action)
            self.cancel_fixtures()
        def r01(row):
            b=self.fixture()
            t=self.ask(row,"What's my schedule next Monday?")
            assert b['uid'] in {x.uid for x in self.chat.conversation.candidates}, 'Next Monday fixture missing'
            assert 'Your Cal.com schedule' in t['answer'], 'No readable listing'
            assert not t['diagnostics'], 'Listing diagnostics'
            local=parse_time(b['start']).astimezone(self.zone)
            assert local.strftime('%I:%M %p').lstrip('0') in t['answer'], 'Wrong local time'
        execute_case('R01',r01)
        def r02(row):
            b=self.fixture()
            self.move_proposal(row,b['uid'],self.start+timedelta(hours=2))
            self.ask(row,'confirm')
            assert any(x.start==self.start+timedelta(hours=2) for x in self.active()),'Move not applied'
            assert all(self.check_google(x.uid)['status']=='verified' for x in self.active()),'Google not verified'
        execute_case('R02',r02)
        def r03(row):
            assert multi, 'No dedicated seminar template'
            b=self.fixture(event=multi)
            t=self.ask(row,"Reschedule next week's "+self.tag+' seminar to 4 PM.')
            assert not t['pending'], 'Prepared without selection'
            number=next((i for i,x in enumerate(self.chat.conversation.candidates,1) if x.uid==b['uid']),None)
            assert number, 'Seminar search did not find fixture'
            t=self.ask(row,f'Meeting number {number}, please.')
            assert t['pending']=='reschedule','Selected seminar has no proposal'
            self.ask(row,'confirm')
            assert any(x.start.astimezone(self.zone).hour==16 for x in self.active()),'4 PM target lost'
            assert all(self.check_google(x.uid)['status']=='verified' for x in self.active()),'Google not verified'
        execute_case('R03',r03)
        def r04(row):
            assert multi, 'No multi-duration template'
            b=self.fixture(event=multi)
            n=self.select_fixture(row,b['uid'])
            before=len(self.writes)
            t=self.ask(row,f'Make meeting number {n} 1 hour, keeping its start time.')
            assert t['pending']=='reschedule','No duration proposal'
            assert len(self.writes)==before,'Wrote before confirmation'
            self.ask(row,'confirm')
            active=self.active()
            assert len(active)==1 and (active[0].end-active[0].start)==timedelta(hours=1),'Wrong duration'
            assert active[0].start==parse_time(b['start']),'Start changed'
            assert self.check_google(active[0].uid)['status']=='verified','Google duration not verified'
        execute_case('R04',r04)
        def r05(row):
            b=self.fixture()
            n=self.select_fixture(row,b['uid']); before=len(self.writes)
            t=self.ask(row,f'Make meeting number {n} 1 hour.')
            assert not t['pending'],'Unsupported duration produced proposal'
            assert len(self.writes)==before,'Unsupported duration wrote'
            assert any(d['code']=='unsupported_duration' for d in t['diagnostics']),'No structured duration rejection'
            assert self.check_google(b['uid'])['status']=='verified','Original Google event changed'
        execute_case('R05',r05)
        def r06(row):
            b=self.fixture()
            self.move_proposal(row,b['uid'],self.start+timedelta(hours=2))
            before=len(self.writes)
            self.ask(row,'Never mind')
            t=self.ask(row,'confirm')
            assert len(self.writes)==before and not t['pending'],'Discarded move executed'
            assert self.client.get_booking(b['uid'])['start']==utc_string(self.start),'Original changed'
        execute_case('R06',r06)
        def r07(row):
            b=self.fixture()
            self.move_proposal(row,b['uid'],self.start+timedelta(hours=2))
            token=self.chat.conversation.pending.token
            revised=self.start+timedelta(hours=3)
            t=self.ask(row,'Actually make that '+revised.astimezone(self.zone).strftime('%I:%M %p')+' instead, same day.')
            assert t['pending']=='reschedule' and self.chat.conversation.pending.token!=token,'Old proposal not replaced'
            self.ask(row,'confirm')
            assert any(x.start==revised for x in self.active()),'Revised move not applied'
            assert all(self.check_google(x.uid)['status']=='verified' for x in self.active()),'Google not verified'
        execute_case('R07',r07)
        def r08(row):
            b=self.fixture()
            self.move_proposal(row,b['uid'],self.start+timedelta(hours=2))
            self.ask(row,'confirm'); after=len(self.writes)
            t=self.ask(row,'confirm')
            assert not t['pending'],'Repeated confirmation prepared another action'
            assert len(self.writes)==after,'Repeated confirmation wrote again'
            assert self.chat.process() is False,'Consumed request replayed'
            # Render and rerender the actual live session without another prompt.
            from unittest.mock import patch
            from streamlit.testing.v1 import AppTest
            with patch('calcom_agent.ui_session.create_session',return_value=self.chat):
                app=AppTest.from_file(str(Path('app.py').resolve())).run()
                app.run()
                assert not app.exception,'UI rerun exception'
            assert len(self.writes)==after,'Browser rerun wrote again'
        execute_case('R08',r08)
        def r09(row):
            first=self.fixture(); second=self.fixture(start=self.start+timedelta(minutes=30))
            t=self.ask(row,f"What's my schedule on {date}?")
            listed=[b.uid for b in self.chat.conversation.candidates]
            assert listed.count(first['uid'])==1 and listed.count(second['uid'])==1,'Distinct bookings merged/duplicated'
            assert self.check_google(first['uid'])['status']=='verified' and self.check_google(second['uid'])['status']=='verified','Google not verified'
        execute_case('R09',r09)
        if not self.only or 'R10' in self.only:
            row={'id':'R10','status':'Blocked','turns':[], 'reason':
                'Direct Google-only cancellation requires the unconnected Google Calendar writer; existing mismatch is checked read-only below.'}
            self.rows.append(row)
            self.chat=self.new_chat()
            existing=[b for b in self.client.list_bookings() if b.status in {'accepted','pending'}
                      and self.day<=b.start<self.day+timedelta(days=1)
                      and self.client.verify_google_calendar({**asdict(b),'start':utc_string(b.start),'end':utc_string(b.end)}).get('reason')=='google_event_cancelled']
            if existing:
                self.ask(row,f"What's my schedule on {date}?")
                listed={b.uid for b in self.chat.conversation.candidates}
                row['existing_mismatch_excluded']=all(b.uid not in listed for b in existing)
            self.save(); print('BLOCKED R10 direct Google writer required; existing mismatch checked',flush=True)
        def r11(row):
            b=self.fixture()
            number=self.select_fixture(row,b['uid'])
            before=len(self.writes)
            t=self.ask(row,f'Can we reschedule meeting number {number} to afternoon?')
            assert 'available slots' in t['answer'],'No afternoon options'
            t=self.ask(row,"What's my schedule next Monday?")
            assert 'Your Cal.com schedule' in t['answer'],'Later listing failed'
            assert not t['diagnostics'],'Later listing diagnostics'
            assert len(self.writes)==before,'Unconfirmed move wrote'
        execute_case('R11',r11)
        def r12(row):
            self.chat.conversation.allow_conflicts=False
            b=self.fixture()
            self.select_fixture(row,b['uid'])
            before=len(self.writes)
            t=self.ask(row,f'Move the test meeting to {date} at 3 AM without allowing overlaps.')
            assert not t['pending'],'Unavailable 3 AM time produced proposal'
            assert len(self.writes)==before,'Unavailable time wrote'
            assert any(d['code']=='slot_unavailable' for d in t['diagnostics']) or 'unavailable' in t['answer'].lower() or 'no available' in t['answer'].lower(),'No unavailability explanation'
            assert self.check_google(b['uid'])['status']=='verified','Original event changed'
        execute_case('R12',r12)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True)
    parser.add_argument('--suite', choices=['core', 'regressions'], default='core')
    parser.add_argument('--only', nargs='*', choices=[f'R{i:02}' for i in range(1,13)], default=[], help='Run selected regression IDs only')
    args=parser.parse_args()
    if args.only and args.suite != 'regressions':
        parser.error('--only requires --suite regressions')
    global REPORT, STATE
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    suffix = '-'.join(args.only) if args.only else 'all'
    REPORT = Path('reports') / f'acceptance-{stamp}-{args.suite}-{suffix}-{uuid4().hex[:6]}.json'
    STATE = REPORT.with_suffix('.state.json')
    run=Run()
    run.only=set(args.only)
    try:
        if args.suite == 'core': run.core()
        else: run.regressions()
    finally:run.cleanup()

if __name__=='__main__':main()

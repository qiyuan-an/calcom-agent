"""Explicit live integration check. Uses real Cal.com, never the mock adapter.

Run with --execute --attendee-email YOUR_CONTROLLED_ADDRESS --attendee-name NAME.
Creates a temporary booking, updates its address, moves it and cancels it.
Optional --check-rejection tests a reserved .invalid address, never replacing it.
"""
import argparse
from datetime import datetime, timedelta, timezone

from calcom_agent.client import CalcomClient, CalcomError
from calcom_agent.config import load_settings
from calcom_agent.scheduling import SchedulingService


def run(client, *, name, email, zone, check_rejection=False):
    event=next(e for e in client.list_event_types() if e.length_in_minutes==30 and e.policy.get('customName')=='{Scheduler first name}/{Organiser first name} 1:1')
    start=datetime.now(timezone.utc)+timedelta(days=1)
    slots=client.available_slots(event.id,start,start+timedelta(days=7),duration=30,time_zone=zone)
    if len(slots)<2: raise ValueError('Need two available slots')
    service=SchedulingService(client)
    uid=None
    try:
        p=service.prepare_create(event.id,slots[0].start,name=name,email=email,time_zone=zone)
        b=service.execute_confirmed(p.token,confirmed=True,executor=client)
        uid=b['uid']
        assert b['verification']['calcom']=='verified'
        raw=client._get(client._uid_path(uid),client.WRITE_VERSION)['data']
        assert any(a['email']==email for a in raw['attendees'])
        print('PASS real create: verified, original attendee email retained',flush=True)
        assert any(b.uid==uid for b in client.list_bookings())
        print('PASS real list',flush=True)
        p=service.prepare_update(uid,location='Calendar agent integration test room')
        updated=service.execute_confirmed(p.token,confirmed=True,executor=client)
        assert updated['verification']['calcom']=='verified'
        print('PASS real update: location read back',flush=True)
        p=service.prepare_reschedule(uid,slots[1].start,reason='Integration verification')
        moved=service.execute_confirmed(p.token,confirmed=True,executor=client)
        uid=moved['uid']
        assert moved['verification']['calcom']=='verified'
        print('PASS real reschedule',flush=True)
    except CalcomError as error:
        uid=getattr(error,'booking_uid',None) or uid
        raise
    finally:
        if uid:
            p=service.prepare_cancel(uid,reason='Integration test cleanup')
            cancelled=service.execute_confirmed(p.token,confirmed=True,executor=client)
            assert cancelled['status']=='cancelled' and client.get_booking(uid)['status']=='cancelled'
            print('PASS real cancel and cleanup',flush=True)
    if check_rejection:
        synthetic='demo@calendar-agent.invalid'
        p=service.prepare_create(event.id,slots[0].start,name='Email rejection test',email=synthetic,time_zone=zone)
        try:
            b=service.execute_confirmed(p.token,confirmed=True,executor=client)
        except CalcomError as error:
            assert error.code=='attendee_email_rejected' and error.status_code==400
            print('PASS real rejection: HTTP 400 attendee_email_rejected; no success returned; no retry',flush=True)
            print('Provider categories:',error.details,flush=True)
        else:
            # Clean up if the server's email policy ever changes.
            p=service.prepare_cancel(b['uid'],reason='Unexpected acceptance cleanup')
            service.execute_confirmed(p.token,confirmed=True,executor=client)
            raise AssertionError('Synthetic email unexpectedly accepted; test booking cleaned up')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True)
    parser.add_argument('--attendee-email',required=True)
    parser.add_argument('--attendee-name',required=True)
    parser.add_argument('--check-rejection',action='store_true')
    args=parser.parse_args()
    settings=load_settings()
    with CalcomClient(settings) as client:
        run(client,name=args.attendee_name,email=args.attendee_email,zone=settings.default_timezone,check_rejection=args.check_rejection)


if __name__=='__main__': main()

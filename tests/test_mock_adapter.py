from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from calcom_agent.mock import MockCalcomAdapter
from calcom_agent.scheduling import SchedulingService, SchedulingError
from calcom_agent.conversation import Conversation
from calcom_agent.model import ModelReply, ToolCall

NOW=datetime(2026,11,13,tzinfo=timezone.utc)
START=NOW+timedelta(days=5)


@pytest.mark.parametrize('email',['marketplace@g.service.com','person@example.test','Any.Case+tag@nonexistent.invalid'])
def test_offline_full_lifecycle_preserves_synthetic_email(email):
    with MockCalcomAdapter() as c:
        s=SchedulingService(c,clock=lambda:NOW)
        p=s.prepare_create(1,START,name='David',email=email,time_zone='UTC')
        assert not c.bookings
        b=s.execute_confirmed(p.token,confirmed=True,executor=c)
        uid=b['uid']
        assert b['verification']['calcom']=='verified'
        assert c.bookings[uid]['attendees'][0]['email']==email
        assert c.list_bookings()[0].uid==uid
        p=s.prepare_update(uid,location='Demo Room 2')
        s.execute_confirmed(p.token,confirmed=True,executor=c)
        assert c.bookings[uid]['location']=='Demo Room 2'
        p=s.prepare_reschedule(uid,START+timedelta(hours=1))
        s.execute_confirmed(p.token,confirmed=True,executor=c)
        assert c.get_booking(uid)['start']==(START+timedelta(hours=1)).isoformat().replace('+00:00','Z')
        p=s.prepare_cancel(uid)
        result=s.execute_confirmed(p.token,confirmed=True,executor=c)
        assert result['status']=='cancelled'
        assert c.bookings[uid]['attendees'][0]['email']==email


def test_malformed_email_still_fails_before_write():
    with MockCalcomAdapter() as c:
        s=SchedulingService(c,clock=lambda:NOW)
        with pytest.raises(SchedulingError,match='invalid_email'):
            s.prepare_create(1,START,name='David',email='not-an-email',time_zone='UTC')
        assert c.bookings=={}


def test_agent_confirmation_and_rejection_preserve_email_without_false_success():
    email='marketplace@g.service.com'
    with MockCalcomAdapter(reject_emails=[email]) as c:
        m=Mock()
        m.chat.side_effect=[ModelReply(calls=(ToolCall('list_event_types',{}),)),
            ModelReply(calls=(ToolCall('prepare_create',{'event_type_id':1,'start':'2026-11-18T10:30:00','name':'David','email':email}),))]
        chat=Conversation(m,c,executor=c,clock=lambda:NOW)
        assert "Reply 'confirm'" in chat.turn('Book David Nov 18 10:30, '+email)
        assert c.bookings=={}
        reply=chat.turn('confirm')
        assert 'rejected' in reply and 'verified by a fresh read' not in reply
        writes=[r for r in c.requests if r['method']=='POST' and r['path']=='bookings']
        assert len(writes)==1 and writes[0]['body']['attendee']['email']==email
        assert c.bookings=={}


def test_agent_workflow_create_update_reschedule_cancel_query_with_synthetic_email():
    email='Marketplace.Team+demo@nonexistent.invalid'
    def call(tool_name,**args): return ModelReply(calls=(ToolCall(tool_name,args),))
    with MockCalcomAdapter() as c:
        m=Mock()
        m.chat.side_effect=[call('list_event_types'),call('prepare_create',event_type_id=1,
            start='2026-11-18T10:30:00',name='David',email=email),
            call('list_bookings'),call('prepare_update',number=1,location='Demo Room 2'),
            call('list_bookings'),call('prepare_reschedule',number=1,start='2026-11-18T11:30:00'),
            call('list_bookings'),call('prepare_cancel',number=1),
            call('list_bookings'),ModelReply('No active bookings remain.')]
        chat=Conversation(m,c,executor=c,clock=lambda:NOW)
        assert "Reply 'confirm'" in chat.turn('Book David November 18 at 10:30, '+email)
        assert not c.bookings
        assert 'verified by a fresh read' in chat.turn('confirm')
        for request in ('Update the location to Demo Room 2','Move it to 11:30','Cancel it'):
            assert "Reply 'confirm'" in chat.turn(request)
            assert 'verified by a fresh read' in chat.turn('confirm')
        chat.turn('List remaining bookings')
        assert len(c.bookings)==1
        row=next(iter(c.bookings.values()))
        assert row['status']=='cancelled' and row['attendees'][0]['email']==email
        creates=[r for r in c.requests if r['method']=='POST' and r['path']=='bookings']
        assert creates[0]['body']['attendee']['email']==email

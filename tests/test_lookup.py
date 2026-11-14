from datetime import datetime, timezone
from unittest.mock import Mock
import pytest

from calcom_agent.client import Booking
from calcom_agent.conversation import Conversation
from calcom_agent.lookup import title_score
from calcom_agent.mock import MockCalcomAdapter
from calcom_agent.model import ModelReply, ToolCall

NOW=datetime(2026,11,13,18,tzinfo=timezone.utc)


def call(tool,**args): return ModelReply(calls=(ToolCall(tool,args),))


def seeded():
    c=MockCalcomAdapter()
    c.events[2]={'id':2,'title':'Marketplace Seminar','slug':'seminar','lengthInMinutes':30,
                 'bookingFields':[],'locations':[], 'customName':'Marketplace Seminar'}
    for start in ['2026-11-17T18:30:00Z','2026-11-19T18:30:00Z']:
        c.execute('create',{'eventTypeId':2,'start':start,
            'attendee':{'name':'Team','email':'team@example.test','timeZone':'America/Los_Angeles'}})
    return c


@pytest.mark.parametrize('query,title,match',[
    ('seminar','Marketplace Seminar',True),('SEMINAR','Marketplace seminar',True),
    ('seminor','Marketplace Seminar',True),('marketplace seminar','Marketplace — Seminar',True),
    ('seminar','Team standup',False),('seminar','Marketplace Webinar',False),
    ('marketplace seminar','Marketing Seminar',False),('', 'Any title',True)])
def test_fuzzy_title_matching(query,title,match):
    assert bool(title_score(query,title)) is match


def test_next_week_seminar_search_choice_and_confirmed_reschedule():
    with seeded() as c:
        m=Mock()
        m.chat.side_effect=[call('search_bookings',query='seminor',start='2026-11-16T00:00:00',end='2026-11-23T00:00:00'),
            call('prepare_reschedule',number=2,start='2026-11-19T16:00:00')]
        chat=Conversation(m,c,executor=c,clock=lambda:NOW)
        writes=len([r for r in c.requests if r['method']=='POST'])
        answer=chat.turn("Reschedule next week's seminar to 4pm")
        assert 'Which meeting' in answer and '1. Marketplace Seminar' in answer and '2. Marketplace Seminar' in answer
        assert '2026-11-17T10:30:00-08:00' in answer
        assert chat.pending is None and m.chat.call_count==1
        assert len([r for r in c.requests if r['method']=='POST'])==writes
        answer=chat.turn('The second one')
        assert 'Move Marketplace Seminar' in answer and "Reply 'confirm'" in answer
        assert len([r for r in c.requests if r['method']=='POST'])==writes
        assert 'verified by a fresh read' in chat.turn('confirm')
        assert c.get_booking('mock-2')['start']=='2026-11-20T00:00:00Z'
        assert c.get_booking('mock-1')['start']=='2026-11-17T18:30:00Z'
        assert 'next Monday through the following Monday' in m.chat.call_args.args[0][0]['content']
        assert '2026-11-16T00:00:00 inclusive to 2026-11-23T00:00:00 exclusive' in m.chat.call_args.args[0][0]['content']


def test_date_and_original_time_search_without_title():
    with seeded() as c:
        chat=Conversation(Mock(),c,clock=lambda:NOW)
        result=chat._dispatch(ToolCall('search_bookings',{'start':'2026-11-19T10:00:00','end':'2026-11-19T11:00:00'}))
        assert len(result['bookings'])==1 and result['bookings'][0]['uid']=='mock-2'


def test_no_results_clears_stale_references_and_describes_checked_range():
    with seeded() as c:
        m=Mock()
        m.chat.side_effect=[call('search_bookings',query='Unrelated',start='2026-11-16T00:00:00',end='2026-11-23T00:00:00'),ModelReply('Cannot find it; provide a booking number.')]
        chat=Conversation(m,c,clock=lambda:NOW)
        chat.candidates=c.list_bookings()
        reply=chat.turn('Move the unrelated seminar to 4pm')
        assert chat.candidates==[]
        assert 'between 2026-11-16' in reply and 'original time' in reply
        assert 'provide a booking number' not in reply


def test_cancelled_and_out_of_range_results_excluded():
    with seeded() as c:
        c.bookings['mock-1']['status']='cancelled'
        chat=Conversation(Mock(),c,clock=lambda:NOW)
        r=chat._dispatch(ToolCall('search_bookings',{'query':'seminar','start':'2026-11-17T00:00:00','end':'2026-11-18T00:00:00'}))
        assert r['bookings']==[]


def test_missing_date_bounds_are_repaired_by_llm_without_asking_user_to_repeat():
    with seeded() as c:
        m=Mock()
        m.chat.side_effect=[call('search_bookings',query='seminar'),ModelReply('What date?'),
            call('search_bookings',query='seminar',start='2026-11-16T00:00:00',end='2026-11-23T00:00:00')]
        chat=Conversation(m,c,clock=lambda:NOW)
        answer=chat.turn("Find next week's seminar")
        assert 'Which meeting' in answer and m.chat.call_count==3
        assert 'What date?' not in answer


def test_next_week_search_excludes_similar_title_in_following_week():
    with seeded() as c:
        c.execute('create',{'eventTypeId':2,'start':'2026-11-24T18:30:00Z',
            'attendee':{'name':'Team','email':'team@example.test','timeZone':'America/Los_Angeles'}})
        chat=Conversation(Mock(),c,clock=lambda:NOW)
        result=chat._dispatch(ToolCall('search_bookings',{'query':'seminor','start':'2026-11-16T00:00:00','end':'2026-11-23T00:00:00'}))
        assert [r['uid'] for r in result['bookings']]==['mock-1','mock-2']

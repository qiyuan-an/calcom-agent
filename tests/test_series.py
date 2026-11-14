from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from calcom_agent.series import prepare, execute, validate_patch
from calcom_agent.scheduling import SchedulingService, SchedulingError
from calcom_agent.client import EventType, Slot, CalcomError
from calcom_agent.conversation import Conversation
from calcom_agent.model import ModelReply, ToolCall

NOW=datetime(2026,11,13,tzinfo=timezone.utc)
DRAFT={'title':'Marketplace team standup','weekdays':[1,3],'time':'10:30','email':'team@example.test','weeks':None}


def service():
    c=Mock()
    c.list_event_types.return_value=[EventType(1,'30 min meeting',30,[],[])]
    c.available_slots.side_effect=lambda _,start,end,**kw:[Slot(start,end)]
    return SchedulingService(c,clock=lambda:NOW),c


def call(**args):
    return ModelReply(calls=(ToolCall('prepare_weekly_schedule',args),))


def test_llm_interprets_end_date_into_structured_tool_and_preserves_draft():
    s,c=service()
    model=Mock()
    model.chat.side_effect=[call(title=DRAFT['title'],weekdays=[1,3],time='10:30'),ModelReply('email?'),
        call(email='team@example.test'),ModelReply('end?'),call(until='2026-12-31')]
    chat=Conversation(model,c,clock=lambda:NOW)
    assert 'email' in chat.turn('schedule weekly standup for marketplace team Tue and Thu 10:30am')
    assert 'how many weeks' in chat.turn('team@example.test')
    answer=chat.turn('until the end of this year')
    assert '14 individual bookings' in answer
    assert 'through 2026-12-31 inclusive' in answer
    assert '2026-12-31T10:30:00-08:00' in answer
    assert chat.pending is not None
    system=model.chat.call_args.args[0][0]['content']
    assert '2026-11-12' in system and 'inclusive absolute end date' in system
    c.execute.assert_not_called()


def test_explicit_end_replaces_week_count_and_weeks_replace_end():
    s,c=service()
    chat=Conversation(Mock(),c,clock=lambda:NOW)
    chat._series_draft={**DRAFT,'weeks':2}
    chat._dispatch(ToolCall('prepare_weekly_schedule',{'until':'2026-12-31'}))
    assert chat._series_draft['weeks'] is None
    chat._dispatch(ToolCall('prepare_weekly_schedule',{'weeks':1}))
    assert 'until' not in chat._series_draft


@pytest.mark.parametrize('patch',[{'until':'end of this year'},{'until':'2026-12-31','weeks':2},{'weekdays':[7]},{'weekdays':[1,1]},{'time':'25:30'}])
def test_structured_boundary_rejects_unresolved_or_invalid_arguments(patch):
    with pytest.raises(SchedulingError): validate_patch(patch)


def test_no_end_produces_clarification_without_writes():
    s,c=service()
    assert 'on what date' in prepare(s,DRAFT,'America/Los_Angeles')
    c.execute.assert_not_called()


def test_dst_preserves_local_wall_time():
    s,c=service()
    s._clock=lambda:datetime(2027,3,5,tzinfo=timezone.utc)
    p=prepare(s,{**DRAFT,'weeks':2},'America/Los_Angeles')
    assert '2027-03-09T10:30:00-08:00' in p.summary
    assert '2027-03-16T10:30:00-07:00' in p.summary


def test_execution_stops_at_first_failure_and_preserves_completed_references():
    c=Mock()
    e=EventType(1,'Standup',30,[],[],policy={'customName':'Standup'})
    c.list_event_types.return_value=[e]
    c.get_event_type.return_value=e
    c.available_slots.side_effect=lambda _,start,end,**kw:[Slot(start,end)]
    c.execute.side_effect=[{'uid':'first'},CalcomError('attendee_email_rejected',400)]
    result=execute(c,{'title':'Standup','starts':['2030-11-19T18:30:00Z','2030-11-21T18:30:00Z','2030-11-26T18:30:00Z'], 'email':'team@example.test','zone':'America/Los_Angeles'})
    assert not result['complete'] and result['series']==[{'uid':'first'}]
    assert c.execute.call_count==2




def test_confirmation_describes_weekdays_time_until_and_requires_explicit_confirmation():
    s,c=service()
    model=Mock()
    model.chat.return_value=call(**{**{k:v for k,v in DRAFT.items() if k!='weeks'},'until':'2026-12-31'})
    chat=Conversation(model,c,executor=Mock(),clock=lambda:NOW)
    answer=chat.turn('Schedule marketplace team standup Tue and Thu at 10:30am until the end of this year, team@example.test')
    assert 'I will create Marketplace team standup meetings every Tue, Thu at 10:30 AM (America/Los_Angeles)' in answer
    assert 'through 2026-12-31 inclusive' in answer
    assert "Reply 'confirm' to execute" in answer
    c.execute.assert_not_called()
    chat.executor.execute.assert_not_called()
    token=chat.pending.token
    # A changed interpretation requires a new preview and invalidates the old approval.
    model.chat.return_value=call(until='2026-12-24')
    revised=chat.turn('Actually end on December 24')
    assert '2026-12-24 inclusive' in revised
    assert chat.pending.token!=token
    chat.executor.execute.assert_not_called()


def test_llm_repairs_conflicting_end_fields_before_confirmation():
    s,c=service()
    m=Mock()
    m.chat.side_effect=[call(**{**DRAFT,'until':'2026-12-31','weeks':52}),ModelReply('Please clarify.'),
                       call(**{**{k:v for k,v in DRAFT.items() if k!='weeks'},'until':'2026-12-31'})]
    chat=Conversation(m,c,clock=lambda:NOW)
    answer=chat.turn('Weekly standup Tue Thu 10:30 until the end of this year, team@example.test')
    assert 'through 2026-12-31 inclusive' in answer
    assert chat.pending is not None
    assert m.chat.call_count==3
    c.execute.assert_not_called()


def test_first_email_rejection_retains_draft_and_gives_correct_next_step():
    s,c=service()
    m=Mock()
    m.chat.return_value=call(**{**{k:v for k,v in DRAFT.items() if k!='weeks'},'until':'2026-12-31'})
    chat=Conversation(m,c,executor=Mock(),clock=lambda:NOW)
    chat.turn('Weekly standup Tue Thu 10:30 until year end, team@example.test')
    chat.scheduling.execute_confirmed=Mock(return_value={'series':[], 'complete':False,
        'error':'attendee_email_rejected','request_rejected':True,'http_status':400,'template_id':42})
    answer=chat.turn('confirm')
    assert 'rejected the first booking request' in answer
    assert 'remaining dates were not attempted' in answer
    assert 'template already exists (ID 42)' in answer
    assert 'Unverified reference: none' not in answer
    assert chat._series_draft['until']=='2026-12-31'
    assert chat.pending is None

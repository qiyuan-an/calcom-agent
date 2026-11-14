"""Explicit offline test adapter. Never selected by production session creation.

Uses the real HTTP client/parser/write verifier with a stateful in-memory server.
Email syntax is checked, DNS/deliverability and email challenges are not simulated.
No socket or provider call is made, and caller-provided addresses are retained.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import re

import httpx

from .client import CalcomClient, ONE_ON_ONE_NAME, parse_time, utc_string
from .config import Settings


class MockCalcomAdapter(CalcomClient):
    def __init__(self, *, reject_emails=()):
        self.bookings = {}
        self.events = {1:{'id':1,'title':'30 minute meeting','slug':'30-min','lengthInMinutes':30,
            'bookingFields':[{'slug':'title','required':True}], 'locations':[], 'customName':ONE_ON_ONE_NAME}}
        self.requests = []
        self.reject_emails = set(reject_emails)
        super().__init__(Settings('synthetic-mock-key','mock-host'), transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        path=request.url.path.removeprefix('/v2/')
        body=json.loads(request.content) if request.content else {}
        self.requests.append({'method':request.method,'path':path,'body':deepcopy(body)})
        def ok(data, **extra):
            return httpx.Response(200,json={'status':'success','data':deepcopy(data),**extra})
        def error(status,message):
            return httpx.Response(status,json={'status':'error','error':{'message':message}})
        if path=='me': return ok({'name':'Mock Host','username':'mock-host'})
        if path=='event-types':
            if request.method=='GET': return ok(list(self.events.values()))
            event={'id':max(self.events)+1,'bookingFields':[{'slug':'title','required':True}],**body}
            self.events[event['id']]=event
            return ok(event)
        if path.startswith('event-types/'):
            event=self.events.get(int(path.split('/')[1]))
            return ok(event) if event else error(404,'Event type not found')
        if path=='slots':
            start,end=parse_time(request.url.params['start']),parse_time(request.url.params['end'])
            duration=int(request.url.params.get('duration',30))
            slots=[]
            while start+timedelta(minutes=duration)<=end:
                occupied=any(b['status'] in {'accepted','pending'} and parse_time(b['start'])<start+timedelta(minutes=duration)
                    and parse_time(b['end'])>start and b['uid']!=request.url.params.get('bookingUidToReschedule') for b in self.bookings.values())
                if not occupied: slots.append({'start':utc_string(start),'end':utc_string(start+timedelta(minutes=duration))})
                start+=timedelta(minutes=duration)
                if len(slots)>=1000: break
            return ok({'slots':slots})
        if path=='bookings' and request.method=='GET':
            return ok(list(self.bookings.values()),pagination={'hasMore':False})
        if path=='bookings' and request.method=='POST':
            email=body['attendee']['email']
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): return error(400,'Invalid email format')
            if email in self.reject_emails: return error(400,'Email domain deliverability check failed')
            event=self.events[body['eventTypeId']]
            start=parse_time(body['start'])
            uid='mock-'+str(len(self.bookings)+1)
            name=body['attendee']['name'].split()[0]
            title=f'{name}/Mock 1:1' if event.get('customName')==ONE_ON_ONE_NAME else event['title']
            row={'uid':uid,'title':title,'eventTypeId':event['id'],'status':'accepted','start':utc_string(start),
                 'end':utc_string(start+timedelta(minutes=body.get('lengthInMinutes',event['lengthInMinutes']))),
                 'attendees':[deepcopy(body['attendee'])],'bookingFieldsResponses':body.get('bookingFieldsResponses',{}),'location':'Cal Video'}
            self.bookings[uid]=row
            return ok(row)
        if path.startswith('bookings/'):
            parts=path.split('/')
            row=self.bookings.get(parts[1])
            if row is None: return error(404,'Booking not found')
            if len(parts)==2: return ok(row)
            if parts[2]=='references': return ok([])  # No Google access in offline tests.
            if parts[2]=='location' and request.method=='PATCH':
                row['location']=body['location']['address']
            elif parts[2]=='reschedule' and request.method=='POST':
                duration=(timedelta(minutes=body['lengthInMinutes']) if 'lengthInMinutes' in body
                          else parse_time(row['end'])-parse_time(row['start']))
                row['start']=body['start']; row['end']=utc_string(parse_time(row['start'])+duration)
            elif parts[2]=='cancel' and request.method=='POST': row['status']='cancelled'
            else: return error(404,'Unsupported mock endpoint')
            return ok(row)
        return error(404,'Unsupported mock endpoint')

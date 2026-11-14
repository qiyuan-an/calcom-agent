"""User-facing failure reports built from validated execution facts."""
from datetime import datetime
from zoneinfo import ZoneInfo

REASONS = {
    'email_undeliverable': 'Cal.com reports that this email address cannot receive mail. Use an address on a working mail domain; this is a provider deliverability rejection, not proof of a syntax error.',
    'email_domain_deliverability': 'Cal.com could not validate the email domain’s mail delivery (DNS/MX check). This does not establish whether a particular mailbox exists.',
    'email_format': 'Cal.com rejected the email address format.',
    'email_validation': 'Cal.com rejected email validation but did not identify a more specific recognized reason.',
    'required_fields': 'Cal.com reported missing required booking information.',
    'availability': 'Cal.com rejected the requested availability or time slot.',
    'recurrence': 'Cal.com rejected the recurrence configuration.',
}


def provider_context(details):
    """Allow only fixed categories; never render arbitrary provider text."""
    result = {}
    if details.get('method') in {'GET','POST','PATCH','DELETE'}: result['method'] = details['method']
    if details.get('resource') in {'event_types','event_type','availability','bookings'}: result['resource'] = details['resource']
    version=details.get('api_version','')
    if isinstance(version,str) and len(version)==10:
        try: datetime.strptime(version,'%Y-%m-%d'); result['api_version']=version
        except ValueError: pass
    result['validation_reasons']=[r for r in details.get('validation_reasons',[]) if isinstance(r,str) and r in REASONS]
    return result


def series_failure(result):
    rows=result['series']
    phase=result.get('phase','booking_creation')
    label={'template_discovery':'finding a meeting template','template_creation':'creating the meeting template',
           'availability_validation':'checking availability','booking_creation':'creating a booking'}.get(phase,'processing the schedule')
    status=result.get('http_status')
    lines=[f"Series stopped while {label} ({result['error']}" + (f", HTTP {status})." if status else ').')]
    if result.get('title'): lines.append('Meeting: '+result['title'])
    if result.get('email'):
        lines.append('Attendee: '+result['email'])
        if '@' in result['email']: lines.append('Email domain: '+result['email'].rsplit('@',1)[1])
    if result.get('failed_start'):
        local=datetime.fromisoformat(result['failed_start'].replace('Z','+00:00')).astimezone(ZoneInfo(result.get('zone','UTC')))
        lines.append(f"Affected occurrence: {local.isoformat()} ({result.get('zone','UTC')}).")
    total=result.get('total')
    lines.append(f"Verified bookings: {len(rows)}" + (f" of {total}." if total is not None else '.'))
    if result.get('attempted') is not None:
        lines.append(f"Booking requests sent: {result['attempted']}; not attempted: {max(0,total-result['attempted'])}.")
    if result.get('template_id'):
        state='created during this attempt' if result.get('template_created') else 'available for reuse'
        lines.append(f"The meeting template already exists (ID {result['template_id']}); {state}.")
    details=provider_context(result.get('provider_details',{}))
    routes={'bookings':'/v2/bookings','event_types':'/v2/event-types','event_type':'/v2/event-types/{id}','availability':'/v2/slots'}
    if details.get('method') and details.get('resource') in routes:
        lines.append(f"API request: {details['method']} {routes[details['resource']]} (version {details.get('api_version','not recorded')}).")
    reasons=details.get('validation_reasons',[])
    lines.extend(REASONS[r] for r in reasons)
    if not reasons: lines.append('Cal.com did not return a recognized detailed validation reason; no more specific cause is confirmed.')
    if result.get('unverified_uid'):
        lines.append(f"Booking may exist but was not verified: {result['unverified_uid']}. Inspect it before any retry.")
    if result['error']=='attendee_email_rejected':
        if not rows and result.get('request_rejected'):
            lines.append('Cal.com rejected the first booking request. No occurrences were verified, and the remaining dates were not attempted.')
        lines.append('Cal.com enforces the email-domain check server-side. The current personal demo account cannot disable it; the override requires organization membership. Disabling notification emails does not bypass validation.')
        lines.append('Use a controlled real mailbox or an alias of your own address. Provide the corrected address and I’ll prepare the same schedule for a new confirmation.' if not rows else
                     'Verify the attendee address, inspect the completed references below, and prepare only the missing occurrences. Do not rerun the whole series.')
    elif phase in {'template_discovery','availability_validation'} and not result.get('attempted'):
        lines.append('No booking requests were sent. Resolve the failed discovery or availability check, then review a new proposal. Any template listed above remains available.')
    elif not result.get('request_rejected'):
        lines.append('The failed request may have taken effect. Inspect Cal.com and the destination calendar before retrying. No write was automatically retried.')
    else:
        lines.append('Correct the rejected booking details and review a new proposal. Preserve the completed bookings below.')
    return '\n'.join(lines)

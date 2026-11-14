from calcom_agent.failures import series_failure, provider_context


def failure(**changes):
    return {'series':[], 'error':'attendee_email_rejected','http_status':400,
            'phase':'booking_creation','failed_start':'2026-11-17T18:30:00Z','zone':'America/Los_Angeles',
            'title':'Marketplace standup','email':'marketplace@g.service.com','total':14,'attempted':1,
            'template_id':42,'template_created':False,'request_rejected':True,
            'provider_details':{'method':'POST','resource':'bookings','api_version':'2026-02-25',
                                'validation_reasons':['email_domain_deliverability'], 'raw':'synthetic-secret'}, **changes}


def test_email_failure_reports_action_context_progress_and_account_limitation():
    report=series_failure(failure())
    for text in ['HTTP 400','Marketplace standup','marketplace@g.service.com','2026-11-17T10:30:00-08:00',
                 'Verified bookings: 0 of 14','requests sent: 1; not attempted: 13','ID 42',
                 'DNS/MX','personal demo account cannot disable','organization membership','corrected address']:
        assert text in report
    assert 'synthetic-secret' not in report
    assert 'Unverified reference: none' not in report


def test_unknown_partial_failure_preserves_uncertainty_and_does_not_offer_full_retry():
    report=series_failure(failure(error='write_outcome_unknown',request_rejected=False,
        series=[{'uid':'verified-first'}],unverified_uid='possible-second',attempted=2,provider_details={}))
    assert 'Verified bookings: 1 of 14' in report
    assert 'possible-second' in report and 'may have taken effect' in report
    assert 'no more specific cause is confirmed' in report


def test_template_failure_does_not_mislabel_booking_or_claim_existing_template():
    report=series_failure(failure(error='forbidden',phase='template_creation',template_id=None,
        failed_start=None,attempted=0,request_rejected=False))
    assert 'creating the meeting template' in report
    assert 'already exists' not in report
    assert 'Affected occurrence' not in report


def test_provider_context_only_exposes_allowlisted_metadata():
    context=provider_context({'method':'POST','resource':'bookings','api_version':'2026-02-25',
        'validation_reasons':['email_format','synthetic-secret',{}],
        'headers':{'Authorization':'secret'},'body':'private','raw_response':'private'})
    assert context=={'method':'POST','resource':'bookings','api_version':'2026-02-25','validation_reasons':['email_format']}

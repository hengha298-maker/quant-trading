from datetime import datetime,date,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import hashlib,json,tempfile,unittest
from investment.market import xshg_sessions
from goose_research import calendar_review as c

def fictional_calendar():
    # Fictional classified days test the parser; never installed as a real year.
    day=date(2027,1,1);days=[]
    while day.year==2027:days.append({'date':day.isoformat(),'status':'open' if len(days)%7<5 else 'closed','basis':'FICTIONAL TEST CLASSIFICATION'});day+=timedelta(days=1)
    doc=c.draft(2027,[{'url':'https://www.sse.com.cn/FICTIONAL-UNIT-TEST','sha256':'a'*64,'publishedAt':'2026-01-01T00:00:00+08:00'}],days,'FICTIONAL TEST')
    doc['reviewedAt']='2026-10-01T12:00:00+08:00';return doc

class CalendarReviewTests(unittest.TestCase):
    def test_draft_and_partial_year_and_future_source_cannot_activate(self):
        r=fictional_calendar()
        with self.assertRaisesRegex(ValueError,'DRAFT'):c.validate_year(r)
        r['status']='reviewed-complete';c.validate_year(r)
        r['days'].pop()
        with self.assertRaisesRegex(ValueError,'COMPLETE_DAILY'):c.validate_year(r)
        r=fictional_calendar();r['status']='reviewed-complete';r['sources'][0]['publishedAt']='2027-12-01T12:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'NOT_AVAILABLE'):c.validate_year(r)
    def test_default_2027_stays_blocked_in_existing_daily_gate(self):
        from investment.daily import _sessions
        with self.assertRaises(ValueError):_sessions('2027-01-04')
        self.assertEqual('2026-12-31',c.supported_through())
    def test_reviewed_explicit_days_extend_the_same_gate_without_weekday_inference(self):
        r=fictional_calendar();r['status']='reviewed-complete';r['days'][0]['status']='closed';r['classificationSha256']=hashlib.sha256(json.dumps(r['days'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        scratch=Path(__file__).resolve().parents[1]/'work/calendar-unit-tests';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as t:
            p=Path(t)/'fixture.json';p.write_text(json.dumps({'version':1,'calendars':[r]}))
            with patch.object(c,'PATH',p):
                self.assertEqual([],xshg_sessions('2027-01-01','2027-01-01'))
                self.assertEqual('2027-12-31',c.supported_through())
                self.assertEqual([d['date'] for d in r['days'][:7] if d['status']=='open'],xshg_sessions('2027-01-01','2027-01-07'))

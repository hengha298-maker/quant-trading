"""No network or user data: public-shape fiction and a temporary read-only SQLite."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from goose_research.cli import WORK, synthetic_dataset, run_research, output_directory, strict_json
from goose_research.backtest import run_comparison
from goose_research.reports import build_reports, blocked_report, pre1445_health, read_v1_status


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(exist_ok=True)
        cls.dataset=synthetic_dataset()

    def test_deterministic_replay_and_legacy_export_are_complete_without_private_fields(self):
        a=run_comparison(self.dataset);b=run_comparison(self.dataset)
        self.assertEqual(a,b)
        legacy,extended=build_reports(self.dataset,a)
        self.assertNotIn('research',legacy)
        self.assertEqual(legacy['signals'][0]['stance'],'watch')
        self.assertEqual(legacy['signals'][0]['quality'],'low')
        self.assertEqual(len(extended['research']['comparison']),4)
        self.assertFalse(extended['research']['decisionAllowed'])
        self.assertEqual(legacy['paperTrades'],[])
        self.assertEqual(set(extended['research']['split']),{'train','validation','test'})

    def test_test_run_writes_all_outputs_only_to_fresh_ignored_directory(self):
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            result=run_research(self.dataset,out=Path(temp)/'run',pre1445=True)
            folder=Path(result['output'])
            self.assertEqual({p.name for p in folder.iterdir()}, {'dataset.json','quality.json','backtest.json','goose-report-v31.json','goose-report-v32.json','pre1445-test.json','bark-dry-run.json','run.json'})
            health=json.loads((folder/'pre1445-test.json').read_text(encoding='utf-8'))
            self.assertFalse(health['notification']['sent'])
            self.assertFalse(health['notification']['phoneArrivalVerified'])
            self.assertFalse(health['scheduleChanged'])
            self.assertEqual(health['strategyObservation']['stance'],'watch')
            with self.assertRaises(FileExistsError):run_research(self.dataset,out=folder)

    def test_blocked_etf_never_produces_returns_or_four_fake_zero_rows(self):
        dataset=synthetic_dataset('515450.SH','etf')
        dataset['quality'].update(status='blocked',unitVerified=False)
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            result=run_research(dataset,out=Path(temp)/'blocked',pre1445=True)
            folder=Path(result['output'])
            self.assertTrue(result['blocked'])
            self.assertFalse((folder/'backtest.json').exists())
            self.assertFalse((folder/'goose-report-v32.json').exists())
            report=json.loads((folder/'goose-report-v31.json').read_text(encoding='utf-8'))
            self.assertIsNone(report['backtest'])
            self.assertEqual(report['signals'][0]['stance'],'watch')

    def test_pre1445_true_time_and_1450_paper_result_are_separate(self):
        bj=timezone(timedelta(hours=8))
        status={'lastRunAt':'2026-10-26T14:50:04+08:00','lastFillAt':None,'readOnly':True}
        before=pre1445_health(self.dataset,now=datetime(2026,10,27,14,40,tzinfo=bj),v1_status=status)
        after=pre1445_health(self.dataset,now=datetime(2026,10,27,15,5,tzinfo=bj),v1_status=status)
        self.assertTrue(before['generatedBeforeCutoff']);self.assertFalse(after['generatedBeforeCutoff'])
        self.assertEqual(before['v1PaperStatus'],status)
        self.assertIn('NO_TODAY_EXECUTABLE_SIGNAL',before['codes'])
        self.assertIn('AFTER_1445_NOT_A_PRE_CUTOFF_REPORT',after['codes'])
        self.assertEqual(before['dataHealth']['through'],'2025-12-31')
        self.assertFalse(after['notification']['sent'])

    def test_v1_read_is_query_only_and_hash_unchanged_no_holdings_exported(self):
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            p=Path(temp)/'fictional.sqlite3'
            connection=sqlite3.connect(p)
            connection.executescript("CREATE TABLE events(ts TEXT);CREATE TABLE fills(ts TEXT);CREATE TABLE positions(secret TEXT);INSERT INTO events VALUES('2026-10-26T14:50:00+08:00');INSERT INTO positions VALUES('FICTIONAL_SECRET');")
            connection.commit();connection.close()
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            result=read_v1_status(p)
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),digest)
            self.assertEqual(result['lastRunAt'],'2026-10-26T14:50:00+08:00')
            self.assertNotIn('FICTIONAL_SECRET',json.dumps(result))
            Path(str(p)+'-wal').write_text('fictional journal',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'BUSY'):read_v1_status(p)

    def test_parser_and_output_guard_reject_duplicates_and_protected_paths(self):
        with self.assertRaises(ValueError):strict_json('{"x":1,"x":2}')
        with self.assertRaises(ValueError):strict_json('{"x":NaN}')
        with self.assertRaisesRegex(ValueError,'OUTPUT_MUST'):output_directory(Path(__file__).resolve().parents[2]/'v3')
        with self.assertRaisesRegex(ValueError,'OUTPUT_MUST'):output_directory(WORK)

    def test_future_calendar_is_rejected_not_approximated_as_weekdays(self):
        from goose_research.market import xshg_sessions
        with self.assertRaisesRegex(ValueError,'2026_12_31'):xshg_sessions('2026-12-31','2027-01-04')


if __name__=='__main__':unittest.main()

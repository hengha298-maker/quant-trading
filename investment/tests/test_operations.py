import tempfile,unittest,json
from unittest.mock import patch
from pathlib import Path
from datetime import datetime,timedelta,timezone
from investment.operations import set_paused,pause_state,automation_status
from investment.daily import run_daily
class OperationTests(unittest.TestCase):
 def test_equal_or_backward_clock_still_reads_last_append(self):
  frozen=datetime.fromisoformat('2026-10-29T14:00:00+08:00')
  with patch('investment.operations._now',return_value=frozen):
   set_paused(self.root,True,confirmed=True);set_paused(self.root,False,confirmed=True)
  with patch('investment.operations._now',return_value=frozen-timedelta(minutes=1)):
   set_paused(self.root,True,confirmed=True)
  self.assertTrue(pause_state(self.root)['paused'])
  with patch('investment.operations._now',return_value=frozen):set_paused(self.root,False,confirmed=True)
  self.assertFalse(pause_state(self.root)['paused'])
  self.assertEqual(4,len(list((self.root/'investment/work/private/automation-controls').glob('*.json'))))
 def setUp(self):
  p=Path(__file__).resolve().parents[1]/'work/operation-tests';p.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=p);self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def test_actual_task_status_is_not_hardcoded_false(self):
  r=automation_status(self.root,task_reader=lambda:{'known':True,'enabled':True,'state':'Ready'})
  self.assertIs(r['enabled'],True);self.assertFalse(r['executionEnabled'])
 def test_pause_blocks_generator_and_notification_without_claim(self):
  set_paused(self.root,True,confirmed=True)
  def forbidden(*a,**k):raise AssertionError('must not generate')
  e=run_daily(self.root,now=datetime(2026,10,29,14,38,tzinfo=timezone(timedelta(hours=8))),generate=forbidden,confirmed_send=True)
  self.assertEqual('LOCAL_ANALYSIS_PAUSED',e['code']);self.assertFalse(e['notification']['sent']);self.assertFalse((self.root/'investment/work/daily/2026-10-29/generation.claim.json').exists())
 def test_pause_and_resume_append_history(self):
  set_paused(self.root,True,confirmed=True);set_paused(self.root,False,confirmed=True)
  self.assertFalse(pause_state(self.root)['paused']);self.assertEqual(2,len(list((self.root/'investment/work/private/automation-controls').glob('*.json'))))
 def test_pause_requires_explicit_boolean_confirmation(self):
  for value in [None,'false',0]:
   with self.assertRaises(ValueError):set_paused(self.root,value,confirmed=True)
 def test_corrupt_pause_fails_closed_without_push(self):
  set_paused(self.root,True,confirmed=True);p=next((self.root/'investment/work/private/automation-controls').glob('*.json'));p.write_text('{}')
  e=run_daily(self.root,now=datetime(2026,10,29,14,38,tzinfo=timezone(timedelta(hours=8))),confirmed_send=True)
  self.assertEqual('LOCAL_CONTROL_INVALID',e['code']);self.assertFalse(e['notification']['sent'])

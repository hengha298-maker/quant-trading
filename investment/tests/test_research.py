from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from copy import deepcopy
import unittest
from investment.engine import metrics
from investment.research import build_research,portfolio_risk,STRATEGIES,register_strategy
from goose_research.market import xshg_sessions
BJ=timezone(timedelta(hours=8))
def fixture():
 days=xshg_sessions('2026-06-01','2026-10-29');rows=[{'date':d,'open':str(D(10)+D(n)/100),'close':str(D(10)+D(n)/100),'high':str(D(10)+D(n)/100+D('.001')),'low':'9','volume':str(100+n)} for n,d in enumerate(days)]
 raw={'symbol':'600900.SH','adjustment':'none','quality':{'status':'validated'},'bars':rows};q=deepcopy(raw);q.update(adjustment='qfq',quality={'status':'validated-basis-only','ohlcAndCalendarValidated':True});return raw,q
class ResearchTests(unittest.TestCase):
 def build(self,raw=None,q=None):
  if raw is None:raw,q=fixture()
  return build_research('600900.SH',raw,q,{'status':'stale-snapshot','availableCash':None,'symbolWeights':[]},datetime(2026,10,29,14,38,tzinfo=BJ))
 def test_ma60_and_completed_day_without_future_bar(self):
  raw,q=fixture();raw['bars'][-1]['close']='9999999';r=self.build(raw,q)
  self.assertEqual('2026-10-28',r['through']);self.assertNotEqual('9999999',r['plugins'][0]['close']);self.assertEqual(60,len(r['priceSeries']));self.assertIsNotNone(r['plugins'][0]['ma60'])
 def test_absent_qfq_pauses_direction_and_dividend_stays_unknown(self):
  raw,_=fixture();r=self.build(raw,None);self.assertEqual('data-insufficient',r['plugins'][0]['state']);self.assertFalse(r['plugins'][2]['totalReturnVerified']);self.assertIsNone(r['plugins'][2]['dividendYield'])
 def test_short_history_no_fake_ma60(self):
  raw,q=fixture();raw['bars']=raw['bars'][-30:];q['bars']=q['bars'][-30:];r=self.build(raw,q)
  self.assertEqual('data-insufficient',r['plugins'][0]['state']);self.assertNotIn('ma60',r['plugins'][0])
 def test_correlation_uses_matching_dates(self):
  raw,_=fixture();other=deepcopy(raw);other['symbol']='515450.SH'
  r=portfolio_risk({'600900.SH':raw,'515450.SH':other},datetime(2026,10,29,14,38,tzinfo=BJ));self.assertEqual('1.00',r['pairs'][0]['correlation']);self.assertIsNone(r['industryOverlap'])
 def test_plugins_do_not_claim_new_strategy_backtest(self):
  r=self.build();self.assertTrue(all(not p['backtestVerified'] for p in r['plugins']));self.assertFalse(r['portfolioSuitability']['orderSizingEnabled'])
 def test_duplicate_plugin_registration_rejected(self):
  with self.assertRaises(ValueError):register_strategy('trend-20-60',lambda c:{})

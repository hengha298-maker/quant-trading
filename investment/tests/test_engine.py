import unittest
from datetime import datetime,timezone
from investment.engine import portfolio,build_analysis,compatible_report
class AnalysisTests(unittest.TestCase):
 def test_missing_holdings_never_uses_old_quantities(self):
  r=build_analysis({'quotes':{},'histories':{}},None,now=datetime(2026,10,28,13,tzinfo=timezone.utc))
  self.assertEqual(r['portfolio']['positions'],[]);self.assertEqual(r['status'],'holdings-required')
  self.assertTrue(all(i['stance']=='watch' for i in r['instruments']))
 def test_unknown_cost_does_not_become_zero(self):
  h={'asOf':'2026-10-28','positions':[{'symbol':'600900.SH','quantity':'101','costPrice':None}]}
  q={'600900.SH':{'price':'1.13','sourceTimestamp':'2026-10-28T15:00:00+08:00','quality':{'status':'closing-snapshot','unitVerified':True}}}
  p=portfolio(h,q,'2026-10-28');self.assertEqual(p['marketValue'],'114.13');self.assertIsNone(p['pnl'])
 def test_stale_holdings_never_relabelled_current(self):
  p=portfolio({'asOf':'2025-01-01','positions':[]},{},'2026-10-28');self.assertEqual(p['status'],'stale-snapshot')
 def test_blocked_units_never_value_holdings(self):
  h={'asOf':'2026-10-28','positions':[{'symbol':'515450.SH','quantity':'731','costPrice':'1.25'}]}
  p=portfolio(h,{'515450.SH':{'price':'123','quality':{'status':'blocked'}}},'2026-10-28');self.assertIsNone(p['marketValue']);self.assertIsNone(p['pnl'])
 def test_phone_report_has_no_fake_fills(self):
  r=build_analysis({'quotes':{},'histories':{}},None);p=compatible_report(r)
  self.assertIsNone(p['backtest']);self.assertEqual(p['paperTrades'],[])
if __name__=='__main__':unittest.main()

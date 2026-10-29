import unittest
from investment.strategy_quality import paired_bootstrap,holm,assess

def curve(rate,n=180):
    from datetime import date,timedelta
    equity=100000000;rows=[]
    for i in range(n):equity=round(equity*(1+rate*(1 if i%17 else -2)));rows.append({'date':(date(2025,1,1)+timedelta(days=i)).isoformat(),'equity':str(equity)})
    return rows

class StrategyQualityTests(unittest.TestCase):
    def test_pairing_deterministic_and_short_samples_explicit(self):
        a=curve(.001);b=curve(.0005)
        first=paired_bootstrap(a,b);self.assertEqual(first,paired_bootstrap(a,b));self.assertEqual(180,first['sessions'])
        self.assertEqual('insufficient-sessions',paired_bootstrap(a[:40],b[:40])['status'])
        self.assertEqual(0,paired_bootstrap(a,a)['meanDailyExcessBps'])
    def test_multiple_comparisons_and_dependence_not_binomial_winner_test(self):
        r=holm([{'pValue':.01},{'pValue':.02},{'pValue':.3},{'pValue':None}])
        self.assertEqual([.04,.06,.6,None],[x['holmAdjustedP'] for x in r])
        empty=assess([]);self.assertFalse(empty['winnerCountIsEvidence']);self.assertEqual('尚未证明优于买入持有',empty['conclusion'])
    def test_even_positive_retrospective_screen_does_not_prove_future_advantage(self):
        base={'metrics':{'totalReturnBps':0},'equityCurve':curve(0)};better={'metrics':{'totalReturnBps':500},'equityCurve':curve(.001)}
        study={'modes':{'price':{'buy_hold':{'partitions':dict.fromkeys(['train','validation','test'],base)},'trend_20_60':{'partitions':dict.fromkeys(['train','validation','test'],better)}}}}
        r=assess([{'symbol':'FICTIONAL','study':study}]);self.assertTrue(r['comparisons'][0]['statisticalScreenPassed'])
        self.assertEqual('not-demonstrated',r['comparisons'][0]['advantageStatus']);self.assertFalse(r['prospectiveValidated'])
    def test_resampling_resolution_can_support_the_full_comparison_family(self):
        r=paired_bootstrap(curve(.001),curve(0));self.assertEqual(4999,r['repetitions']);self.assertLessEqual(r['minimumResolvableP'],.05/47)
        family=holm([{'pValue':r['minimumResolvableP']} for _ in range(47)]);self.assertLess(family[0]['holmAdjustedP'],.05)
    def test_time_or_incomplete_family_never_produces_partial_significance(self):
        from datetime import datetime,timezone,timedelta
        base={'metrics':{'totalReturnBps':0},'equityCurve':curve(0)};better={'metrics':{'totalReturnBps':500},'equityCurve':curve(.001)}
        study={'modes':{'price':{'buy_hold':{'partitions':dict.fromkeys(['train','validation','test'],base)},'trend_20_60':{'partitions':dict.fromkeys(['train','validation','test'],better)}}}}
        items=[{'symbol':'FICTIONAL','study':study}]
        expired=assess(items,deadline=datetime.now(timezone.utc)-timedelta(seconds=1));self.assertEqual([],expired['comparisons']);self.assertFalse(expired['familyComplete'])
        incomplete=assess(items,expected_family_size=47);self.assertEqual([],incomplete['comparisons']);self.assertIn('INCOMPLETE',incomplete['reason'])

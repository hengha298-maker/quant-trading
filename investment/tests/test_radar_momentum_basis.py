"""Fictional opposite-sign qfq/raw paths; never reads user holdings or network."""
from copy import deepcopy
from decimal import Decimal as D
import unittest
from unittest.mock import patch
from investment import radar,strategy_lab
from investment.tests.test_radar import market


class RadarMomentumBasisTests(unittest.TestCase):
    def setUp(self):
        self.market=market();self.symbol='510001.SH';self.benchmark=self.market['histories']['510000.SH']
        self.h=self.market['histories'][self.symbol]
        # One high raw close 20 sessions ago makes raw momentum negative while
        # qfq momentum and both current MA trends stay positive. It is not a
        # corporate-action claim; this is an adversarial fictional input.
        old=self.h['raw']['bars'][-21];price=D(self.h['raw']['bars'][-1]['close'])+D('.01')
        old.update(open=str(price),high=str(price+D('.01')),low=str(price-D('.01')),close=str(price))

    def feature(self,h=None,b=None):return radar.features(h or self.h,b or self.benchmark,{})

    def test_same_raw_call_supplies_both_momenta_and_preserves_other_factors(self):
        f=self.feature();q=strategy_lab.indicators(self.h['adjusted']['bars'],self.benchmark['adjusted']['bars'],len(self.h['adjusted']['bars']))
        raw=strategy_lab.indicators(self.h['raw']['bars'],self.benchmark['raw']['bars'],len(self.h['raw']['bars']))
        self.assertGreater(q['momentum20'],0);self.assertLess(raw['momentum20'],0)
        self.assertEqual(raw['momentum20'],f['momentum20']);self.assertEqual(raw['relative60'],f['relative60'])
        for key in set(q)-{'momentum20','relative60'}:self.assertEqual(q[key],f[key],key)
        self.assertEqual(D(self.h['adjusted']['bars'][-1]['close'])/D(self.h['adjusted']['bars'][-121]['close'])-1,f['momentum120'])
        self.assertFalse(f['adjustmentDirectionConflict'])

    def test_score_and_candidate_gate_use_raw_not_qfq_twenty_day_momentum(self):
        portfolio={'asOf':'2026-10-29','status':'stale-snapshot','positions':[{'symbol':'510000.SH'}],'concentration':None}
        with patch('investment.radar.cached_suite',return_value={'strategies':{'buy_hold':{}},'fixture':True}),patch('investment.trust_research.research',return_value=({'status':'unavailable','fixture':True},None)):
            result=radar.build('unused',self.market,portfolio)
            # Same fixture with the old bug restored must pass its momentum
            # gate and become a candidate, proving the gate is material.
            original=radar.features
            def mixed(h,b,held):
                f=original(h,b,held);f['momentum20']=strategy_lab.indicators(h['adjusted']['bars'],b['adjusted']['bars'],len(h['adjusted']['bars']))['momentum20'];return f
            with patch('investment.radar.features',side_effect=mixed):old=radar.build('unused',self.market,portfolio)
        item=next(i for i in result['items'] if i['symbol']==self.symbol);before=next(i for i in old['items'] if i['symbol']==self.symbol)
        self.assertEqual(before['score']['value']-5,item['score']['value']);self.assertTrue(before['eligible'])
        self.assertFalse(item['eligible']);self.assertTrue(any('20日未复权' in x for x in item['rejections']))
        self.assertIn(self.symbol,[i['symbol'] for i in old['groups']['etf']['candidates']])
        self.assertNotIn(self.symbol,[i['symbol'] for i in result['groups']['etf']['candidates']])
        self.assertEqual(radar.FACTOR_BASIS,result['factorBasis']);self.assertEqual(2,result['modelRevision'])
        basis=next(p['basis'] for p in item['score']['parts'] if p['factor']=='动量与相对强弱')
        self.assertIn('20日标的未复权',basis);self.assertIn('120日前复权',basis)

    def test_qfq_changes_do_not_change_raw_momenta_but_still_change_trend(self):
        old=self.feature();h=deepcopy(self.h);b=deepcopy(self.benchmark)
        for dataset in (h,b):
            for i,row in enumerate(dataset['adjusted']['bars']):
                for field in ('open','high','low','close'):row[field]=str(D(row[field])*(1+D(i)/1000))
        changed=self.feature(h,b)
        for key in ('momentum20','relative60'):self.assertEqual(old[key],changed[key])
        for key in ('ma20','ma60','momentum120','vol60'):self.assertNotEqual(old[key],changed[key])

    def test_subject_and_benchmark_raw_changes_have_separate_expected_effects(self):
        old=self.feature()
        for side in ('subject','benchmark'):
            h=deepcopy(self.h);b=deepcopy(self.benchmark);target=h if side=='subject' else b
            target['raw']['bars'][-1]['close']=str(D(target['raw']['bars'][-1]['close'])*D('1.01'))
            f=self.feature(h,b);self.assertNotEqual(old['relative60'],f['relative60'])
            if side=='subject':self.assertNotEqual(old['momentum20'],f['momentum20'])
            else:self.assertEqual(old['momentum20'],f['momentum20'])
            for key in ('ma20','ma60','momentum120','vol60','drawdown126'):self.assertEqual(old[key],f[key])

    def test_raw_calendar_mismatch_duplicates_order_and_missing_bars_block(self):
        for side in ('subject','benchmark'):
            for mutation in ('missing','shift','duplicate','reorder'):
                h=deepcopy(self.h);b=deepcopy(self.benchmark);rows=(h if side=='subject' else b)['raw']['bars']
                if mutation=='missing':rows.pop()
                elif mutation=='shift':rows[-1]['date']='2026-10-30'
                elif mutation=='duplicate':rows[-2]['date']=rows[-1]['date']
                else:rows[-2],rows[-1]=rows[-1],rows[-2]
                with self.subTest(side=side,mutation=mutation),self.assertRaisesRegex(ValueError,'RAW_DATES_MISMATCH'):self.feature(h,b)
        # Equal but invalid date arrays must not sneak through equality alone.
        h=deepcopy(self.h);b=deepcopy(self.benchmark)
        for d in (h,b):
            for basis in ('raw','adjusted'):d[basis]['bars'][-2]['date']=d[basis]['bars'][-1]['date']
        with self.assertRaisesRegex(ValueError,'RAW_DATES_MISMATCH'):self.feature(h,b)

    def test_benchmark_unverified_raw_or_wrong_basis_blocks(self):
        for field,value in [('adjustment','qfq'),('quality',{'status':'blocked'})]:
            b=deepcopy(self.benchmark);b['raw'][field]=value
            with self.assertRaisesRegex(ValueError,'RAW_BASIS_UNVALIDATED'):self.feature(b=b)

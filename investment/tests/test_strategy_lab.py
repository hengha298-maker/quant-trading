"""Offline fictional prices only. Never reads account or production caches."""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import tempfile,unittest
from investment import strategy_lab as lab
from goose_research.market import xshg_sessions
from goose_research.backtest import _prepare


def history(symbol='510300.SH',kind='etf'):
    days=xshg_sessions('2024-05-10','2026-10-29')
    rows=[]
    for i,day in enumerate(days):
        p=D('10')+D(i)/100
        rows.append({'date':day,'open':str(p),'high':str(p+D('.01')),'low':str(p-D('.01')),'close':str(p),'volume':100000000,'publishedAt':None})
    raw={'format':'goose-market-dataset','version':1,'symbol':symbol,'kind':kind,'adjustment':'none','currency':'CNY','priceUnit':'CNY-per-share',
         'bars':rows,'calendar':{'name':'XSHG','sessions':days},'quality':{'status':'synthetic','unitVerified':True}}
    return {'raw':raw,'adjusted':{'symbol':symbol,'adjustment':'qfq','bars':deepcopy(rows),'quality':{'status':'validated-basis-only','ohlcAndCalendarValidated':True}}}


SPEC={'symbol':'510050.SH','kind':'etf','tick':'0.001','limitBps':1000}


class StrategyLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h=history('510050.SH');cls.b=history();cls.result=lab.run_suite(cls.h,cls.b,SPEC,allow_synthetic=True)

    def test_synthetic_never_enters_production(self):
        with self.assertRaisesRegex(ValueError,'SYNTHETIC'):lab.run_suite(self.h,self.b,SPEC)

    def test_five_rules_same_partitions_costs_and_cents(self):
        r=self.result
        self.assertEqual(set(lab.STRATEGIES),set(r['strategies']))
        self.assertFalse(r['parametersOptimized']);self.assertFalse(r['completeTotalReturn'])
        for strategy,s in r['strategies'].items():
            self.assertEqual(set(lab.PARTITIONS),set(s['partitions']))
            for fold,f in s['partitions'].items():
                self.assertTrue(lab.PARTITIONS[fold]['from']<=f['from']<=f['to']<=lab.PARTITIONS[fold]['to'])
                self.assertIsNone(f['metrics']['annualizedReturnBps'])
                self.assertEqual('0% annual; 252-session risk scaling',f['metrics']['riskFreeRateAssumption'])
                for t in f['trades']:
                    self.assertEqual(0,t['quantity']%100)
                    self.assertGreaterEqual(int(t['commission']),500)
                    self.assertEqual('0',t['stampDuty'])
                    self.assertEqual(0,D(t['price'])%D('.1'))

    def test_decisions_do_not_access_execution_or_future_bar(self):
        rows=self.h['adjusted']['bars'];bench=self.b['adjusted']['bars'];i=100
        before=lab.indicators(rows,bench,i);changed=deepcopy(rows);changed[i]['close']='99999';changed[i+1]['volume']=0
        self.assertEqual(before,lab.indicators(changed,bench,i))
        self.assertEqual(rows[i-1]['date'],before['signalAsOf'])

    def test_t1_and_stock_sell_tax_and_etf_tick_rounding(self):
        p=lab.TickPortfolio(dict(lab.CONFIG),'stock','600036.SH','.01')
        p.buy('2026-10-28',D('10'),p.cash);quantity=p.quantity
        p.sell('2026-10-28',D('11'));self.assertEqual(quantity,p.quantity)
        p.sell('2026-10-29',D('11'));self.assertEqual(0,p.quantity)
        self.assertGreater(int(p.trades[-1]['stampDuty']),0)
        q=lab.TickPortfolio(dict(lab.CONFIG),'etf','510050.SH','.001')
        self.assertEqual(D('1.202'),q._quote('BUY',100,D('1.201'))[0])

    def test_opening_limit_and_slippage_outside_limit_block_fill(self):
        h=deepcopy(self.h);i=100;old=D(h['raw']['bars'][i-1]['close']);upper=(old*D('1.1')).quantize(D('.001'))
        for opening,reason in [(upper,'opening-price-limit-queue-unmodelled'),(upper-D('.001'),'slippage-fill-outside-price-limits')]:
            h['raw']['bars'][i].update(open=str(opening),high=str(opening),low=str(opening),close=str(opening))
            bars,_=_prepare(h['raw']);signals={i:lab.indicators(h['adjusted']['bars'],self.b['adjusted']['bars'],i)}
            r=lab._run('buy_hold',bars,h['raw'],signals,SPEC,[i],set())
            self.assertEqual([],r['trades']);self.assertEqual(reason,r['orders'][0]['reason'])

    def test_delayed_publication_and_action_day_stop_orders(self):
        h=deepcopy(self.h);i=100;day=h['raw']['bars'][i]['date'];signals={i:lab.indicators(h['adjusted']['bars'],self.b['adjusted']['bars'],i)}
        bars,_=_prepare(h['raw']);r=lab._run('buy_hold',bars,h['raw'],signals,SPEC,[i],{day})
        self.assertEqual('unverified-corporate-action-limit-reference',r['orders'][0]['reason'])
        h['raw']['bars'][i-1]['publishedAt']=day+'T15:01:00+08:00';bars,_=_prepare(h['raw'])
        self.assertEqual([],lab._run('buy_hold',bars,h['raw'],signals,SPEC,[i],set())['orders'])

    def test_short_history_and_wrong_basis_rejected(self):
        h=deepcopy(self.h);h['adjusted']['adjustment']='none'
        with self.assertRaisesRegex(ValueError,'BASIS'):lab.run_suite(h,self.b,SPEC,allow_synthetic=True)
        h=deepcopy(self.h);h['adjusted']['bars'].pop()
        with self.assertRaisesRegex(ValueError,'DATES'):lab.run_suite(h,self.b,SPEC,allow_synthetic=True)

    def test_cache_hash_includes_rule_revision_and_all_prices(self):
        before=lab.input_hash(self.h,self.b,SPEC);h=deepcopy(self.h);h['raw']['bars'][-1]['volume']+=1
        self.assertNotEqual(before,lab.input_hash(h,self.b,SPEC))
        scratch=Path(__file__).resolve().parents[1]/'work/lab-unit-tests';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            with self.assertRaisesRegex(ValueError,'SYNTHETIC'):lab.cached_suite(root,self.h,self.b,SPEC)

    def test_market_proxy_cannot_be_its_own_relative_momentum_evidence(self):
        spec={**SPEC,'symbol':'510300.SH'}
        r=lab.run_suite(self.b,self.b,spec,allow_synthetic=True)
        self.assertFalse(r['strategies']['relative_momentum_60']['applicable'])

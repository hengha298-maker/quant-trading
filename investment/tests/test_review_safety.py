"""Review regressions: fictional records, isolated stores, no real sends/orders."""
from copy import deepcopy
from decimal import Decimal as D
import unittest
from unittest.mock import patch
from investment import ledger,ledger_accounting as accounting,control,market,total_return,trust_research
from investment.tests import test_ledger_trust as journal
from investment.tests.test_benchmark_basis import fixtures


class WarmupCoverageTests(unittest.TestCase):
    def test_2025_account_coverage_does_not_certify_2024_indicator_warmup(self):
        for symbol,kind in [('600900.SH','stock'),('512660.SH','etf')]:
            with self.subTest(symbol=symbol):
                a,b,doc,days=fixtures();original=a['symbol'];a.update(symbol=symbol,kind=kind)
                doc['events']=[]
                for c in doc['coverage']:
                    c.update(eventIds=[],cashPerShareSum='0')
                    if c['symbol']==original:c.update(symbol=symbol,**{'from':'2025-01-01'})
                spec={'symbol':symbol,'kind':kind,'tick':'.01' if kind=='stock' else '.001','limitBps':1000}
                r=total_return.compare(a,b,spec,doc,start='2025-01-02',end='2025-12-31',allow_synthetic=True)
                self.assertTrue(r['coverage']['complete']);self.assertFalse(r['indicatorActionCoverage']['complete'])
                for mode in ('cash','reinvest'):
                    self.assertIsNotNone(r['modes'][mode]['buy_hold']['metrics'])
                    for strategy in ('trend_20_60','breakout_20_10','low_vol_drawdown'):
                        item=r['modes'][mode][strategy];self.assertEqual('blocked',item['status']);self.assertIsNone(item['metrics'])
                        self.assertIn('WARMUP',item['notApplicableReason']);self.assertEqual([],item['equityCurve'])
                    self.assertFalse(r['modes'][mode]['relative_momentum_60']['applicable'])
                for item in r['modes']['price'].values():self.assertIsNotNone(item['metrics'])
                doc['coverage'][0]['from']=days[0]
                complete=total_return.compare(a,b,spec,doc,start='2025-01-02',end='2025-12-31',allow_synthetic=True)
                self.assertTrue(complete['indicatorActionCoverage']['complete'])
                self.assertIsNotNone(complete['modes']['cash']['trend_20_60']['metrics'])
                self.assertEqual(r['modes']['cash']['buy_hold'],complete['modes']['cash']['buy_hold'])
        self.assertEqual(4,trust_research.REVISION)


class ProjectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.t=journal.TrustTests();self.t.setUp();self.addCleanup(self.t.tearDown)
        self.account={'asOf':'2026-10-26','source':{'broker':self.t.broker},'availableCash':'1000','positions':[{'symbol':'600900.SH','quantity':0}]}
    def project(self,batches,day='2026-10-28'):return ledger.project(self.account,batches,day)

    def test_executions_never_become_posted_cash_even_with_net_and_cash_date(self):
        batches=self.t.batch([journal.trade('B1','买入',10,10,1)],source_role='executions')
        r=self.project(batches);self.assertEqual(10,r['projectedQuantities']['600900.SH'])
        self.assertTrue(r['coverage']['executions']['completeDeclared']);self.assertFalse(r['coverage']['cash']['completeDeclared'])
        self.assertIsNone(r['cashFlowChange']);self.assertIsNone(r['cashReconciliationBridge'])
        issues=[];self.assertEqual([],accounting.cash_postings(batches[0]['events'],issues))

    def test_linked_cash_counted_once_and_cash_source_does_not_cover_positions(self):
        self.t.batch([journal.trade('B1','买入',10,10,1)],source_role='executions')
        cash=journal.event('C1','买入',成交金额=101,资金发生额=-101,关联流水号='B1')
        batches=self.t.batch([cash],source_role='cash');r=self.project(batches)
        self.assertEqual('899.00',r['cashReconciliationBridge']);self.assertEqual('-101.00',r['cashFlowChange'])
        cashonly=[b for b in batches if b['sourceRole']=='cash'];r=self.project(cashonly)
        self.assertFalse(r['coverage']['executions']['completeDeclared']);self.assertIsNone(r['cashReconciliationBridge'])
        self.assertIn('UNMATCHED_SETTLEMENT',{x['code'] for x in r['cashIssues']})

    def test_cash_date_not_trade_date_and_missing_date_blocks_bridge(self):
        row=journal.trade('B1','买入',10,10,1);row['资金到账日']='2026-10-28';row['结算日']='2026-10-28'
        batches=self.t.batch([row]);r=self.project(batches,'2026-10-27')
        self.assertEqual('0.00',r['cashFlowChange']);self.assertEqual('1000.00',r['cashReconciliationBridge'])
        self.assertEqual('899.00',self.project(batches)['cashReconciliationBridge'])
        batches=deepcopy(batches);batches[0]['events'][0]['cashDate']=None
        self.assertIsNone(self.project(batches)['cashReconciliationBridge'])

    def test_prior_trade_settles_in_period_and_fee_is_not_double_debited(self):
        trade=journal.trade('B1','买入',10,10,1);batches=self.t.batch([trade],source_role='executions')
        cash=journal.event('C1','买入',when='2026-10-28',成交金额=101,资金发生额=-101,关联流水号='B1')
        batches=self.t.batch([cash],source_role='cash');self.account['asOf']='2026-10-27';self.account['positions'][0]['quantity']=10
        r=self.project(batches);self.assertEqual('-101.00',r['cashFlowChange']);self.assertEqual(10,r['projectedQuantities']['600900.SH'])
        # Reuse the existing full cash/dividend/included-tax scenario.
        self.account['asOf']='2026-10-26';self.account['positions'][0]['quantity']=100
        events=self.t.full()+[journal.event('TAX2','红利税',when='2026-10-28',成交金额=2,费用已含流水号='D1')]
        preview=ledger.parse_events(journal.raw_events(events),'.csv',broker=self.t.broker,coverage_start='2026-10-27',coverage_end='2026-10-28',complete=True)
        r=self.project([preview]);self.assertEqual('1103.00',r['cashReconciliationBridge'])

    def test_partial_cash_coverage_and_separate_domains_stay_unknown(self):
        batches=self.t.batch([journal.event('IN','银证转入',成交金额=100,资金发生额=100)],source_role='cash',complete=False)
        r=self.project(batches);self.assertIsNone(r['cashFlowChange']);self.assertIsNone(r['cashReconciliationBridge'])
        self.assertFalse(r['coverage']['executions']['completeDeclared'])

    def test_unmatched_settlement_is_reported_and_skipped_without_key_error(self):
        for reference in (None,'NOT-FOUND'):
            event={'id':'C1','date':'2026-10-27','cashDate':'2026-10-27','kind':'SETTLEMENT','gross':'101','netCash':'-101','linkId':reference,'sourceRole':'cash'}
            issues=[];posted=accounting.cash_postings([event],issues)
            self.assertEqual([],posted);self.assertIn('UNMATCHED_SETTLEMENT',{i['code'] for i in issues})
            batch={'broker':self.t.broker,'completeCoverageDeclared':True,'coverageStart':'2026-10-27','coverageEnd':'2026-10-28','sourceRole':'combined','events':[event]}
            a=journal.statement(self.t.broker,'2026-10-26',0,0,0,1000);b=journal.statement(self.t.broker,'2026-10-28',0,0,0,1000)
            result=accounting.reconcile_period(a,b,[batch]);self.assertIsNone(result['returns']['accountTotalPnl'])
            self.assertIsNone(self.project([batch])['cashReconciliationBridge'])


class StartupPauseTests(unittest.TestCase):
    def test_paused_and_corrupt_controls_fail_closed_unpaused_allows(self):
        for paused in (True,False):
            with patch('investment.operations.pause_state',return_value={'paused':paused}):self.assertEqual(not paused,control.startup_analysis_allowed('unused'))
        with patch('investment.operations.pause_state',side_effect=ValueError('PAUSE_STORE_INVALID')):self.assertFalse(control.startup_analysis_allowed('unused'))


class RawStockPrecisionTests(unittest.TestCase):
    def dataset(self,bars,quote):return market._dataset('600900.SH',bars,'2026-10-28','2026-10-28',{'receivedAt':'2026-10-28T16:00:00+08:00'},quote)
    def rows(self):return [{'date':'2026-10-28','open':'29.00','high':'29.50','low':'28.90','close':'29.40','volume':10000,'publishedAt':None}]
    def quote(self,price='29.40'):return {'quality':{'status':'closing-snapshot'},'sourceTimestamp':'2026-10-28T15:00:00+08:00','price':price}
    def test_each_raw_ohlc_tick_is_checked_and_no_rounding(self):
        for field in ('open','high','low','close'):
            rows=self.rows();rows[0][field]=str(D(rows[0][field])+D('.001'))
            r=self.dataset(rows,self.quote())
            self.assertFalse(r['quality']['unitVerified']);self.assertEqual('blocked',r['quality']['status'])
    def test_same_date_scale_and_unavailable_scale_are_not_overclaimed(self):
        good=self.dataset(self.rows(),self.quote());self.assertTrue(good['quality']['sameDateQuoteScaleVerified'])
        r=self.dataset(self.rows(),self.quote('294.00'));self.assertFalse(r['quality']['unitVerified']);self.assertEqual('failed',r['quality']['scaleCheckStatus'])
        r=self.dataset(self.rows(),None);self.assertTrue(r['quality']['pricePrecisionVerified']);self.assertIsNone(r['quality']['sameDateQuoteScaleVerified']);self.assertEqual('not-available',r['quality']['scaleCheckStatus'])

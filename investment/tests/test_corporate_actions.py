"""Fictional event accounts only; no personal holdings or receipts."""
from copy import deepcopy
from datetime import datetime,timedelta
from decimal import Decimal as D
import unittest
from pathlib import Path
import tempfile,hashlib
from unittest.mock import patch
from investment.corporate_actions import validate,coverage,causal_series,valuation_rows
from investment.total_return import simulate,compare
from investment.tests.test_strategy_lab import history

SPEC={'symbol':'515450.SH','kind':'etf','tick':'0.001','limitBps':1000}
CFG={'initialCash':100000,'commissionBps':0,'slippageBps':0,'stampDutyBps':0,'minCommission':0}

def fixture(kind='cash-dividend',ratio='2'):
    raw=history('515450.SH')['raw'];rows=raw['bars'][:100]
    for i,r in enumerate(rows):
        p='10' if i<66 else '9' if kind=='cash-dividend' else '5'
        r.update(open=p,close=p,high=str(D(p)+1),low=str(D(p)-1))
    event={'id':'FICTIONAL-event','symbol':SPEC['symbol'],'type':kind,'status':'verified','announcementDate':rows[60]['date'],'availableFrom':rows[61]['date']+'T09:30:00+08:00','recordDate':rows[65]['date'],'exDate':rows[66]['date'],'payDate':rows[70]['date'],'cashPerShare':'1','shareMultiplier':ratio,'shareAvailableDate':rows[66]['date'],'sourceIds':['fixture']}
    raw['bars']=rows;raw['calendar']['sessions']=[r['date'] for r in rows]
    doc={'format':'goose-corporate-actions','version':1,'sources':{'fixture':{'url':'https://www.sse.com.cn/fictional-unit-test.pdf','sha256':'a'*64,'publicationDate':rows[60]['date'],'locator':'FICTIONAL UNIT TEST','file':'fictional.pdf'}},'events':[event],
      'coverage':[{'symbol':SPEC['symbol'],'from':rows[0]['date'],'to':rows[-1]['date'],'status':'reviewed-complete-interval','types':['cash-dividend','share-ratio'],'eventIds':[event['id']],'sourceIds':['fixture']}]}
    bench={'rows':deepcopy(rows),'events':[],'dataset':raw}
    return rows,event,raw,doc,bench

class CorporateActionTests(unittest.TestCase):
    def test_issuer_payable_rollforward_matches_dated_cash_events(self):
        rows,e,raw,doc,b=fixture();c=doc['coverage'][0];c['cashPerShareSum']='1'
        c['issuerCashReconciliationCNY']={'openingPayable':'200','declared':'1000','closingPayable':'200','paid':'1000','shares':'1000'}
        validate(doc,synthetic=True);c['issuerCashReconciliationCNY']['closingPayable']='201'
        with self.assertRaisesRegex(ValueError,'RECONCILIATION_CONFLICT'):validate(doc,synthetic=True)

    def test_comparison_has_five_rules_fair_dates_and_separate_cash_modes(self):
        rows,e,raw,doc,b=fixture();full=history('515450.SH')['raw'];benchmark=history()['raw']
        doc['coverage'][0]['to']=full['bars'][-1]['date']
        r=compare(full,benchmark,SPEC,doc,allow_synthetic=True)
        self.assertEqual({'price','cash','reinvest'},set(r['modes']))
        self.assertFalse(r['usesVendorQfq']);self.assertFalse(r['pointInTimeVintageCertified'])
        for name in r['modes']['price']:
            p=r['modes']['price'][name];c=r['modes']['cash'][name]
            if c['applicable']:self.assertEqual([v['date'] for v in p['equityCurve']],[v['date'] for v in c['equityCurve']])
            else:self.assertEqual('blocked',c['status'])
            self.assertEqual(set(p['partitions']),set(c['partitions']))
        doc['coverage']=[]
        blocked=compare(full,benchmark,SPEC,doc,allow_synthetic=True)
        self.assertEqual({'price'},set(blocked['modes']));self.assertTrue(blocked['coverage']['missingIntervals'])

    def test_provisional_event_cannot_be_complete_coverage(self):
        rows,e,raw,doc,b=fixture();e['status']='provisional'
        self.assertFalse(coverage(doc,SPEC['symbol'],rows[61]['date'],rows[90]['date'])['complete'])

    def test_broken_identity_and_units_fail_before_return_claim(self):
        rows,e,raw,doc,b=fixture();h=history('515450.SH')['raw'];bm=history()['raw'];h['currency']='USD'
        with self.assertRaises(ValueError):compare(h,bm,SPEC,doc,allow_synthetic=True)
        h['symbol']='510050.SH'
        with self.assertRaisesRegex(ValueError,'IDENTITY'):compare(h,bm,SPEC,doc,allow_synthetic=True)

    def test_bonus_listing_delay_preserves_cost_and_allows_final_odd_lot_sale(self):
        rows,e,raw,doc,b=fixture('share-ratio','1.1');e['shareAvailableDate']=rows[70]['date']
        for row in rows[66:]:row.update(open='9.09',high='10',low='8',close='9.09')
        # Direct account verifies the listing boundary and remaining odd lot.
        from investment.total_return import EventPortfolio,CONFIG
        p=EventPortfolio({**CONFIG,**CFG},SPEC);p.buy(rows[61]['date'],D(10),p.cash);p.shares(e,100)
        self.assertEqual(110,p.quantity);self.assertEqual(100000,p.bookCost)
        p.sell(rows[67]['date'],D('9.09'));self.assertEqual(10,p.quantity)
        p.sell(rows[69]['date'],D('9.09'));self.assertEqual(10,p.quantity)
        p.sell(rows[70]['date'],D('9.09'));self.assertEqual(0,p.quantity);self.assertEqual(0,p.bookCost)
    def test_schema_conflict_future_source_and_missing_payment(self):
        rows,e,raw,doc,b=fixture();validate(doc,synthetic=True)
        bad=deepcopy(doc);bad['events'].append(dict(e,id='duplicate-other-id'))
        with self.assertRaisesRegex(ValueError,'CONFLICT'):validate(bad,synthetic=True)
        bad=deepcopy(doc);bad['sources']['fixture']['publicationDate']=rows[80]['date']
        with self.assertRaisesRegex(ValueError,'FUTURE_DOCUMENT'):validate(bad,synthetic=True)
        e['payDate']=None;self.assertFalse(coverage(doc,SPEC['symbol'],rows[61]['date'],rows[90]['date'])['complete'])

    def test_cash_receivable_preserves_ex_day_nav_and_payment_not_double_counted(self):
        rows,e,raw,doc,b=fixture();indexes=list(range(61,85))
        price=simulate(rows,b,SPEC,[e],'buy_hold',indexes,mode='price',config=CFG)
        total=simulate(rows,b,SPEC,[e],'buy_hold',indexes,mode='cash',config=CFG)
        self.assertEqual(-1000,price['metrics']['totalReturnBps']);self.assertEqual(0,total['metrics']['totalReturnBps'])
        ex=next(r for r in total['equityCurve'] if r['date']==e['exDate']);self.assertEqual('10000',ex['dividendReceivable']);self.assertEqual('100000',ex['equity'])
        paid=next(r for r in total['equityCurve'] if r['date']==e['payDate']);self.assertEqual('0',paid['dividendReceivable']);self.assertEqual('100000',paid['equity'])
        self.assertEqual('10000',total['metrics']['dividendNetPaid'])

    def test_rights_survive_sale_and_ex_date_buyer_gets_no_dividend(self):
        rows,e,raw,doc,b=fixture();i=61
        def rule(*args):
            signal=args[1]
            return 'SELL' if signal['signalAsOf']==rows[66]['date'] else 'BUY' if signal['signalAsOf']==rows[60]['date'] else 'HOLD'
        with patch('investment.total_return.decision',side_effect=rule):r=simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',config=CFG)
        self.assertEqual(0,r['equityCurve'][-1]['quantity']);self.assertEqual('100000',r['equityCurve'][-1]['cash'])
        exbuyer=simulate(rows,b,SPEC,[e],'buy_hold',list(range(66,85)),mode='cash',config=CFG)
        self.assertEqual('0',exbuyer['metrics']['dividendGrossPaid'])

    def test_reinvest_only_after_payment_and_whole_lot_with_explicit_fees(self):
        rows,e,raw,doc,b=fixture();cfg={**CFG,'initialCash':10000000}
        r=simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='reinvest',config=cfg)
        by={x['date']:x for x in r['equityCurve']}
        self.assertEqual(10000,by[e['payDate']]['quantity']);self.assertEqual(11100,by[rows[71]['date']]['quantity'])
        self.assertEqual('10000',r['equityCurve'][-1]['cash']);self.assertEqual(0,r['metrics']['totalReturnBps'])
        self.assertTrue(all(t['quantity']%100==0 for t in r['trades']))

    def test_split_and_cost_continuity_and_fractional_reverse_split_block(self):
        rows,e,raw,doc,b=fixture('share-ratio')
        r=simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',config=CFG)
        self.assertEqual(200,r['equityCurve'][-1]['quantity']);self.assertEqual('100000',r['equityCurve'][-1]['bookCost']);self.assertEqual(0,r['metrics']['totalReturnBps'])
        e['shareMultiplier']='0.3333'
        with self.assertRaisesRegex(ValueError,'FRACTIONAL'):simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',config=CFG)

    def test_explicit_tax_reserve_reduces_dividend_once(self):
        rows,e,raw,doc,b=fixture()
        r=simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',tax_bps=2000,config=CFG)
        self.assertEqual('10000',r['metrics']['dividendGrossPaid']);self.assertEqual('2000',r['metrics']['dividendTaxAssumed']);self.assertEqual('8000',r['metrics']['dividendNetPaid']);self.assertEqual(-200,r['metrics']['totalReturnBps'])

    def test_future_events_never_rescale_earlier_indicator_rows(self):
        rows,e,raw,doc,b=fixture();before=causal_series(rows[:66],[e]);after=causal_series(rows,[e]);self.assertEqual(before['bars'],after['bars'][:66])
        self.assertEqual(D(10),D(after['bars'][66]['close']))
        e['availableFrom']=rows[67]['date']+'T09:30:00+08:00'
        self.assertEqual([e['id']],causal_series(rows,[e])['lateAnnouncementIds'])
        with self.assertRaisesRegex(ValueError,'POINT_TIME'):simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',config=CFG)

    def test_unknown_gap_blocks_official_halt_is_nontradable_mark(self):
        rows,e,raw,doc,b=fixture();missing=rows[70]['date'];raw['bars']=[r for r in rows if r['date']!=missing]
        with self.assertRaisesRegex(ValueError,'UNEXPLAINED'):valuation_rows(raw,[])
        halt={'type':'suspension','startDate':missing,'endDate':missing,'resumeDate':rows[71]['date']}
        aligned=valuation_rows(raw,[halt]);mark=next(r for r in aligned if r['date']==missing)
        self.assertFalse(mark['tradable']);self.assertTrue(mark['valuationCarriedForward']);self.assertEqual(0,mark['volume']);self.assertEqual(99,len(raw['bars']))

    def test_late_published_benchmark_does_not_block_unrelated_buy_hold(self):
        rows,e,raw,doc,b=fixture();b['rows'][60]['publishedAt']=rows[61]['date']+'T15:00:00+08:00'
        r=simulate(rows,b,SPEC,[],'buy_hold',[61],config=CFG)
        self.assertEqual(1,len(r['trades']))

    def test_original_source_hash_required_and_cached_certificate_rechecked(self):
        from investment.trust_research import _study
        rows,e,raw,doc,b=fixture();h=history('515450.SH')['raw'];bm=history()['raw']
        h['quality']['status']=bm['quality']['status']='validated'
        doc['coverage'][0]['to']=h['bars'][-1]['date']
        content=b'FICTIONAL ORIGINAL FOR HASH TEST ONLY';doc['sources']['fixture']['sha256']=hashlib.sha256(content).hexdigest()
        scratch=Path(__file__).resolve().parents[1]/'work/event-unit-tests';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as t:
            root=Path(t);folder=root/'investment/work/corporate-actions/official';folder.mkdir(parents=True);p=folder/'fictional.pdf';p.write_bytes(content)
            a,key=_study(root,h,bm,SPEC,doc,'2024-09-02',h['bars'][-1]['date'],None)
            self.assertTrue(a['coverage']['complete']);self.assertIn('cash',a['modes'])
            p.write_bytes(b'FICTIONAL TAMPERED DOCUMENT')
            degraded,new_key=_study(root,h,bm,SPEC,doc,'2024-09-02',h['bars'][-1]['date'],None)
            self.assertNotEqual(key,new_key);self.assertEqual({'price'},set(degraded['modes']))
            blocked=compare(h,bm,SPEC,doc,source_root=root)
            self.assertEqual({'price'},set(blocked['modes']));self.assertFalse(blocked['coverage']['complete'])

    def test_event_during_unpriced_halt_blocks_return_claim(self):
        rows,e,raw,doc,b=fixture();rows[66]['valuationCarriedForward']=True;rows[66]['tradable']=False
        with self.assertRaisesRegex(ValueError,'ACTION_DURING_HALT'):
            simulate(rows,b,SPEC,[e],'buy_hold',list(range(61,85)),mode='cash',config=CFG)

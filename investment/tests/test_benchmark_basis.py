"""Fictional symmetric ex-dividend paths. No real account or network access."""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import hashlib,tempfile,unittest
from investment.tests.test_strategy_lab import history
from investment.total_return import compare,simulate
from investment.benchmark_basis import policy,RELATIVE,PRICE_BASIS,TOTAL_BASIS
from investment.trust_research import _study
from investment import strategy_lab as lab

SPEC={'symbol':'515450.SH','kind':'etf','tick':'0.001','limitBps':1000}

def fixtures():
    a=history('515450.SH')['raw'];b=history()['raw'];days=a['calendar']['sessions']
    sources={k:{'url':'https://www.sse.com.cn/FICTIONAL-'+k+'.pdf','file':k+'.pdf','sha256':hashlib.sha256(k.encode()).hexdigest(),'publicationDate':days[20],'locator':'FICTIONAL TEST ONLY'} for k in ('subject','benchmark')}
    events=[]
    for symbol,key,cash in [('515450.SH','subject','1'),('510300.SH','benchmark','0.5')]:
        events.append({'id':key,'symbol':symbol,'type':'cash-dividend','status':'verified','announcementDate':days[20],'availableFrom':days[21]+'T09:30:00+08:00','recordDate':days[99],'exDate':days[100],'payDate':days[103],'cashPerShare':cash,'sourceIds':[key]})
    for dataset,cash in [(a,D(1)),(b,D('.5'))]:
        for i,row in enumerate(dataset['bars']):
            price=D(10)-(cash if i>=100 else 0);row.update(open=str(price),high=str(price+D(1)),low=str(price-D(1)),close=str(price))
    doc={'format':'goose-corporate-actions','version':1,'sources':sources,'events':events,'coverage':[
      {'symbol':e['symbol'],'from':days[0],'to':days[-1],'status':'reviewed-complete-interval','types':['cash-dividend','share-ratio'],'eventIds':[e['id']],'sourceIds':e['sourceIds'],'cashPerShareSum':e['cashPerShare']} for e in events]}
    return a,b,doc,days

def run(a,b,doc,**kw):return compare(a,b,SPEC,doc,allow_synthetic=True,**kw)

class BenchmarkBasisTests(unittest.TestCase):
    def test_missing_benchmark_blocks_total_only_and_other_four_are_identical(self):
        a,b,doc,days=fixtures();valid=run(a,b,doc);missing=deepcopy(doc);missing['coverage']=[c for c in doc['coverage'] if c['symbol']!=b['symbol']]
        result=run(a,b,missing)
        self.assertTrue(result['modes']['price'][RELATIVE]['applicable'])
        for mode in ('cash','reinvest'):
            row=result['modes'][mode][RELATIVE];self.assertFalse(row['applicable']);self.assertIsNone(row['metrics']);self.assertEqual([],row['equityCurve']);self.assertIn('BENCHMARK',row['notApplicableReason'])
            for strategy in set(lab.STRATEGIES)-{RELATIVE}:
                self.assertEqual(valid['modes'][mode][strategy],result['modes'][mode][strategy])
        self.assertEqual(valid['modes']['price'][RELATIVE],result['modes']['price'][RELATIVE])

    def test_both_covered_series_use_same_gross_event_basis_price_uses_raw(self):
        a,b,doc,days=fixtures();p=policy(doc,a['symbol'],b['symbol'],days[0],days[-1],None,synthetic=True);self.assertTrue(p['totalAllowed'])
        bench={'dataset':b,'subjectDataset':a,'rows':b['bars'],'events':[doc['events'][1]],'relativePolicy':p};captured=[]
        def observe(strategy,signal,kind):captured.append(signal);return 'HOLD'
        for mode,expected,basis in [('price',D(9)/10/(D('9.5')/10)-1,PRICE_BASIS),('cash',D(0),TOTAL_BASIS),('reinvest',D(0),TOTAL_BASIS)]:
            with patch('investment.total_return.decision',side_effect=observe):r=simulate(a['bars'],bench,SPEC,[doc['events'][0]],RELATIVE,[120],mode=mode)
            self.assertAlmostEqual(expected,captured[-1]['relative60']);self.assertEqual(basis,r['signalBasis'])

    def test_full_warmup_and_subject_coverage_are_required(self):
        a,b,doc,days=fixtures();doc['coverage'][0]['from']='2024-09-02'
        result=run(a,b,doc);self.assertTrue(result['coverage']['complete']);self.assertFalse(result['relativeMomentumPolicy']['totalAllowed'])
        self.assertLess(result['relativeMomentumPolicy']['requiredFrom'],result['from']);self.assertFalse(result['modes']['cash'][RELATIVE]['applicable'])

    def test_payment_missing_and_provisional_benchmark_do_not_enable_total(self):
        for field,value in [('payDate',None),('status','provisional')]:
            a,b,doc,days=fixtures();doc['events'][1][field]=value
            result=run(a,b,doc);self.assertFalse(result['modes']['cash'][RELATIVE]['applicable']);self.assertIsNotNone(result['modes']['cash']['buy_hold']['metrics'])

    def test_late_benchmark_announcement_blocks_total_relative_only(self):
        a,b,doc,days=fixtures();doc['events'][1]['availableFrom']=days[101]+'T09:30:00+08:00';result=run(a,b,doc)
        self.assertIn('EVENT_NOT_AVAILABLE',result['modes']['cash'][RELATIVE]['notApplicableReason']);self.assertTrue(result['modes']['cash']['buy_hold']['applicable'])

    def test_direct_total_relative_cannot_bypass_policy_and_delayed_quote_checks(self):
        a,b,doc,days=fixtures();bench={'dataset':b,'subjectDataset':a,'rows':b['bars'],'events':[]}
        with self.assertRaisesRegex(ValueError,'COVERAGE_REQUIRED'):simulate(a['bars'],bench,SPEC,[],RELATIVE,[61],mode='cash')
        b['bars'][60]['publishedAt']=days[61]+'T15:00:00+08:00'
        relative=simulate(a['bars'],bench,SPEC,[],RELATIVE,[61]);self.assertEqual([],relative['trades'])
        independent=simulate(a['bars'],bench,SPEC,[],'buy_hold',[61]);self.assertEqual(1,len(independent['trades']))

    def test_benchmark_original_hash_and_cached_proof_rechecked_without_overwrite(self):
        a,b,doc,days=fixtures();a['quality']['status']=b['quality']['status']='validated'
        scratch=Path(__file__).resolve().parents[1]/'work/benchmark-unit-tests';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as t:
            root=Path(t);folder=root/'investment/work/corporate-actions/official';folder.mkdir(parents=True)
            for name in ('subject','benchmark'):(folder/(name+'.pdf')).write_bytes(name.encode())
            valid,key=_study(root,a,b,SPEC,doc,'2024-09-02',days[-1],None);old=(root/'investment/work/radar/labs'/str(key+'.json')).read_bytes();self.assertTrue(valid['modes']['cash'][RELATIVE]['applicable'])
            (folder/'benchmark.pdf').write_bytes(b'FICTIONAL CORRUPTION')
            result,new_key=_study(root,a,b,SPEC,doc,'2024-09-02',days[-1],None)
            self.assertNotEqual(key,new_key);self.assertFalse(result['modes']['cash'][RELATIVE]['applicable']);self.assertIn('benchmark',result['relativeMomentumPolicy']['benchmarkCoverage']['missingSourceIds'])
            self.assertEqual(old,(root/'investment/work/radar/labs'/str(key+'.json')).read_bytes())
            for s in set(lab.STRATEGIES)-{RELATIVE}:self.assertEqual(valid['modes']['cash'][s],result['modes']['cash'][s])
            (folder/'benchmark.pdf').write_bytes(b'benchmark')
            restored,restored_key=_study(root,a,b,SPEC,doc,'2024-09-02',days[-1],None)
            self.assertEqual(key,restored_key);self.assertTrue(restored['modes']['cash'][RELATIVE]['applicable'])

    def test_legacy_lab_uses_raw_relative_and_independent_rules_do_not_change(self):
        a,b,doc,days=fixtures();h={'raw':a,'adjusted':{'symbol':a['symbol'],'adjustment':'qfq','bars':deepcopy(a['bars']),'quality':{'status':'validated-basis-only','ohlcAndCalendarValidated':True}}};bm={'raw':b,'adjusted':{'symbol':b['symbol'],'adjustment':'qfq','bars':deepcopy(b['bars']),'quality':{'status':'validated-basis-only','ohlcAndCalendarValidated':True}}}
        r=lab.run_suite(h,bm,SPEC,allow_synthetic=True);altered=deepcopy(bm)
        for i,row in enumerate(altered['adjusted']['bars']):
            for field in ('open','high','low','close'):row[field]=str(D(row[field])*(D(1)+D(i)/1000))
        other=lab.run_suite(h,altered,SPEC,allow_synthetic=True)
        self.assertEqual(r['strategies'],other['strategies']);self.assertEqual(PRICE_BASIS,r['strategies'][RELATIVE]['signalBasis'])
        changed=deepcopy(bm);changed['raw']['bars'][100]['volume']+=1;self.assertNotEqual(lab.input_hash(h,bm,SPEC),lab.input_hash(h,changed,SPEC))

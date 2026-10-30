import json,tempfile,unittest,hashlib
from pathlib import Path
from copy import deepcopy
from investment import ledger

HEADER='成交编号,成交日期,操作,证券代码,成交数量,成交价格,成交金额,总费用,资金发生额,已实现盈亏\n'
def account():return {'asOf':'2026-10-26','confirmedAt':'2026-10-26T16:00:00+08:00','source':{'broker':'华宝证券','kind':'manual'},'availableCash':'1200','positions':[{'symbol':'600900.SH','quantity':100,'costPrice':'9','reportedPnl':'12','reportedMarketValue':'1000'}]}
class LedgerTests(unittest.TestCase):
 def setUp(self):
  parent=Path(__file__).resolve().parents[1]/'work/ledger-tests';parent.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def preview(self,rows='T1,2026-10-27,买入,600900,50,10,500,,,\n',broker='华宝证券',complete=True):
  return ledger.parse_events((HEADER+rows).encode(),'.csv',broker=broker,coverage_start='2026-10-27',coverage_end='2026-10-28',complete=complete)
 def save(self,p=None):return ledger.confirm(p or self.preview(),self.root,confirmed=True)
 def test_preview_and_explicit_confirmation_no_raw_identity_saved(self):
  p=self.preview();self.assertFalse(ledger.store(self.root).exists())
  with self.assertRaises(ValueError):ledger.confirm(p,self.root)
  self.save(p);self.assertEqual(1,len(ledger.load_batches(self.root)))
 def test_duplicates_and_overlap_not_counted_twice(self):
  self.save()
  with self.assertRaisesRegex(ValueError,'已存在'):self.save()
  p=self.preview('T1,2026-10-27,买入,600900,50,10,500,,,\nT2,2026-10-28,卖出,600900,10,11,110,1,109,19\n')
  r=self.save(p);self.assertEqual((1,1),(r['newCount'],r['duplicateCount']))
  v=ledger.project(account(),ledger.load_batches(self.root),'2026-10-28');self.assertEqual(140,v['projectedQuantities']['600900.SH']);self.assertEqual('19.00',v['brokerReportedRealizedPnl'])
 def test_conflicting_same_id_never_overwrites(self):
  self.save();p=self.preview('T1,2026-10-27,买入,600900,60,10,600,,,\n')
  with self.assertRaisesRegex(ValueError,'冲突'):self.save(p)
  self.assertEqual(50,ledger.load_batches(self.root)[0]['events'][0]['quantity'])
 def test_unknown_fees_cash_and_realized_stay_unknown(self):
  self.save();p=ledger.project(account(),ledger.load_batches(self.root),'2026-10-28')
  self.assertIsNone(p['cashFlowChange']);self.assertIsNone(p['bookCashProjection']);self.assertIsNone(p['availableCash']);self.assertIsNone(p['brokerReportedRealizedPnl'])
 def test_dividend_gross_is_not_assumed_net_of_unknown_tax(self):
  self.save(self.preview('D1,2026-10-27,分红,600900,,,20,,,\n'))
  self.assertIsNone(ledger.project(account(),ledger.load_batches(self.root),'2026-10-28')['cashFlowChange'])
 def test_explicit_net_cash_reconciles_book_cash_not_available_cash(self):
  self.save(self.preview('T1,2026-10-27,买入,600900,50,10,500,2,-502,\n'))
  p=ledger.project(account(),ledger.load_batches(self.root),'2026-10-28');self.assertIsNone(p['cashReconciliationBridge']);self.assertIn('CASH_DATE_UNKNOWN',{i['code'] for i in p['cashIssues']});self.assertIsNone(p['bookCashProjection']);self.assertIsNone(p['availableCash'])
 def test_brokers_with_same_trade_id_are_isolated(self):
  self.save();self.save(self.preview(broker='银河证券'));self.assertEqual(2,len(ledger.load_batches(self.root)))
 def test_same_day_base_event_is_not_applied(self):
  self.save();a=account();a['asOf']='2026-10-27';p=ledger.project(a,ledger.load_batches(self.root),'2026-10-28')
  self.assertEqual(100,p['projectedQuantities']['600900.SH']);self.assertEqual(1,p['sameDayUnapplied']);self.assertNotEqual('complete-declared-not-broker-verified',p['projectionStatus'])
 def test_partial_quantity_not_promoted_or_cost_invented(self):
  self.save(self.preview(complete=False));a=account();old=deepcopy(a);new=ledger.apply_events(self.root,[a],through='2026-10-28')[0]
  self.assertIsNone(new['positions'][0]['quantity']);self.assertIsNone(new['positions'][0]['costPrice']);self.assertEqual(old,a)
 def test_complete_declared_quantity_is_provisional_not_live_broker_cost(self):
  self.save();a=account();new=ledger.apply_events(self.root,[a],through='2026-10-28')[0]
  self.assertEqual(150,new['positions'][0]['quantity']);self.assertIsNone(new['positions'][0]['availableQuantity']);self.assertIsNone(new['positions'][0]['costPrice']);self.assertEqual('2026-10-26',new['asOf']);self.assertEqual(100,a['positions'][0]['quantity'])
 def test_negative_projection_blocks(self):
  self.save(self.preview('T1,2026-10-27,卖出,600900,200,10,2000,1,1999,\n'))
  p=ledger.project(account(),ledger.load_batches(self.root),'2026-10-28');self.assertIsNone(p['projectedQuantities']);self.assertEqual('conflict',p['projectionStatus'])
 def test_reconciliation_difference_and_match_are_quantity_only(self):
  self.save();p=ledger.project(account(),ledger.load_batches(self.root),'2026-10-28');a=account()
  self.assertEqual('quantity-difference',ledger.reconcile(p,a)['status']);a['positions'][0]['quantity']=150
  self.assertEqual('quantity-matches',ledger.reconcile(p,a)['status'])
 def test_undo_preserves_original_and_removes_projection_effect(self):
  saved=self.save();f=next(ledger.store(self.root).glob('*.json'));before=hashlib.sha256(f.read_bytes()).hexdigest()
  ledger.undo(self.root,saved['batchId'],confirmed=True);self.assertEqual([],ledger.load_batches(self.root));self.assertEqual(before,hashlib.sha256(f.read_bytes()).hexdigest());self.assertEqual(1,len(ledger.load_batches(self.root,include_void=True)))
 def test_store_corruption_stops_instead_of_skipping(self):
  self.save();next(ledger.store(self.root).glob('*.json')).write_text('{}')
  with self.assertRaises(ValueError):ledger.load_batches(self.root)
 def test_duplicate_file_rows_rejected(self):
  with self.assertRaisesRegex(ValueError,'重复'):self.preview('T1,2026-10-27,买入,600900,50,10,500,,,\n'*2)
 def test_formula_and_unrecognized_direction_rejected(self):
  for row in ['T1,2026-10-27,买入,600900,50,10,=500,,,\n','T1,2026-10-27,撤单,600900,50,10,500,,,\n']:
   with self.assertRaises(ValueError):self.preview(row)
 def test_preview_tampering_rejected(self):
  p=self.preview();p['events'][0]['quantity']=999
  with self.assertRaises(ValueError):self.save(p)
 def test_fee_components_require_complete_evidence_and_do_not_double_count(self):
  header=HEADER.strip()+',佣金,印花税,过户费,其他费用\n'
  raw=(header+'F2,2026-10-27,卖出,600900,10,10,100,2,98,,1,0.5,0.5,0\n').encode()
  p=ledger.parse_events(raw,'.csv',broker='华宝证券',coverage_start='2026-10-27',coverage_end='2026-10-28',complete=True)
  self.assertEqual('2',p['events'][0]['fees']);self.assertEqual('0.5',p['events'][0]['stampTax'])
 def test_cash_direction_conflict_and_extreme_notional_are_rejected(self):
  for row in ['F2,2026-10-27,买入,600900,10,10,100,1,101,\n','F2,2026-10-27,买入,600900,999999999999,999999999,,, ,\n']:
   with self.assertRaises(ValueError):self.preview(row)
 def test_evidence_change_invalidates_cached_report(self):
  from investment.engine import account_evidence_digest,mark_report_freshness
  from datetime import datetime,timezone,timedelta
  old=account_evidence_digest(self.root);self.save()
  r={'id':'fictional','generatedAt':'2026-10-29T14:38:00+08:00','asOf':'2026-10-29','accountEvidenceDigest':old,'portfolio':{},'instruments':[],'warnings':[]}
  visible=mark_report_freshness(r,root=self.root,now=datetime(2026,10,29,14,39,tzinfo=timezone(timedelta(hours=8))))
  self.assertEqual('account-source-changed',visible['status']);self.assertNotIn('status',r)
 def test_restored_duplicate_file_and_json_list_fail_closed(self):
  self.save();p=next(ledger.store(self.root).glob('*.json'));alias=p.with_name('duplicate.json');alias.write_bytes(p.read_bytes())
  with self.assertRaises(ValueError):ledger.load_batches(self.root)
  alias.unlink();p.write_text('[]')
  with self.assertRaises(ValueError):ledger.load_batches(self.root)

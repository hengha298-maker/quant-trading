"""Synthetic accounting evidence, not acceptance of any real broker export."""
import csv,io,json,tempfile,unittest,hashlib
from pathlib import Path
from copy import deepcopy
from investment import ledger,statements,ledger_accounting as accounting,ledger_backup as backup
from investment.holdings import parse_manual,commit_preview,load_latest_by_broker

HEAD=['成交编号','交易日','成交日','成交时间','结算日','资金到账日','操作','证券代码','成交数量','成交价格','成交金额','总费用','资金发生额','关联流水号','费用已含流水号','已实现盈亏']
def raw_events(rows):
    stream=io.StringIO();w=csv.DictWriter(stream,HEAD,lineterminator='\n');w.writeheader();w.writerows(rows);return stream.getvalue().encode()
def event(i,kind,*,when='2026-10-27',cash_date=None,**kw):
    return {'成交编号':i,'交易日':when,'成交日':when,'结算日':when,'资金到账日':cash_date or when,'操作':kind,**kw}
def trade(i,kind,q,p,fee,when='2026-10-27'):
    from decimal import Decimal as D
    gross=D(str(q))*D(str(p));net=-gross-D(str(fee)) if kind=='买入' else gross-D(str(fee))
    return event(i,kind,when=when,证券代码='600900',成交数量=q,成交价格=p,成交金额=str(gross),总费用=str(fee),资金发生额=str(net))
def statement(broker,asof,q,cost,mv,cash):
    raw=f'证券代码,持仓数量,可卖数量,持仓成本金额,证券市值,账面现金,可用资金,冻结资金\n600900,{q},{q},{cost},{mv},{cash},{cash},0\n'.encode()
    return statements.parse_statement(raw,'.csv',broker=broker,as_of=asof,cost_basis='moving-average-acquisition-including-buy-fees',complete_account=True)

class TrustTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/'work/trust-tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.temp.name);self.broker='华宝证券'
    def tearDown(self):self.temp.cleanup()
    def batch(self,rows,**kw):
        doc=ledger.parse_events(raw_events(rows),'.csv',broker=self.broker,coverage_start='2026-10-27',coverage_end='2026-10-28',complete=kw.pop('complete',True),**kw)
        ledger.confirm(doc,self.root,confirmed=True);return ledger.load_batches(self.root)
    def pair(self):return statement(self.broker,'2026-10-26',100,1000,1000,1000),statement(self.broker,'2026-10-28',120,'1280.8',1680,1103)
    def full(self):
        return [trade('B1','买入',50,12,1),trade('S1','卖出',30,13,1,when='2026-10-28'),
          event('D-ENT','红利应收',证券代码='600900',成交金额=20,关联流水号='DIV-1'),
          event('D1','分红',when='2026-10-28',证券代码='600900',成交金额=20,总费用=2,资金发生额=18,关联流水号='DIV-1'),
          event('F1','手续费',when='2026-10-28',成交金额=3,资金发生额=-3),
          event('IN1','银证转入',成交金额=500,资金发生额=500),event('OUT1','银证转出',when='2026-10-28',成交金额=200,资金发生额=-200)]
    def test_full_period_cost_cash_dividend_and_external_capital_reconcile(self):
        opening,closing=self.pair();result=accounting.reconcile_period(opening,closing,self.batch(self.full()))
        self.assertEqual([],result['issues']);self.assertEqual('reconciled-declared-coverage',result['status'])
        self.assertEqual('1103.00',result['cash']['projectedClosing']);self.assertEqual('0.00',result['cash']['difference'])
        p=result['positions'][0];self.assertEqual(120,p['projectedQuantity']);self.assertEqual('1280.80',p['acquisitionCost'])
        self.assertEqual('68.80',p['realizedPnl']);self.assertEqual('399.20',p['unrealizedPnl'])
        r=result['returns'];self.assertEqual('483.00',r['accountTotalPnl']);self.assertEqual('300.00',r['externalNetInflow']);self.assertEqual('468.00',r['pricePnlChange'])
        self.assertIsNone(r['holdingPeriodReturnPct']);self.assertIsNotNone(r['modifiedDietzEstimatedPct']);self.assertIsNone(r['timeWeightedReturn']);self.assertIsNone(r['moneyWeightedReturn'])
        self.assertFalse(r['sinceInceptionVerified']);self.assertEqual('18.00',r['creditedDividendNetBeforeSeparateTax'])
    def test_first_duplicate_incremental_sell_dividend_fee_transfer_and_undo(self):
        rows=self.full();self.batch(rows[:1]);before=backup.export_state(self.root)
        with self.assertRaises(ValueError):self.batch(rows[:1])
        self.batch(rows[:2]);self.assertEqual(2,len(ledger.load_batches(self.root)))
        self.batch(rows);self.assertEqual(7,sum(len(b['events']) for b in ledger.load_batches(self.root)))
        latest=max(ledger.load_batches(self.root),key=lambda b:b['confirmedAt']);original=(ledger.store(self.root)/(latest['batchId']+'.json')).read_bytes()
        ledger.undo(self.root,latest['batchId'],confirmed=True);self.assertEqual(2,sum(len(b['events']) for b in ledger.load_batches(self.root)))
        self.assertEqual(original,(ledger.store(self.root)/(latest['batchId']+'.json')).read_bytes());self.assertEqual(1,len(before['files']))
    def test_partial_fills_have_unique_executions_not_one_order(self):
        rows=[trade('FILL-1','买入',10,10,1),trade('FILL-2','买入',20,10,1)]
        batches=self.batch(rows);a=statement(self.broker,'2026-10-26',0,0,0,1000);b=statement(self.broker,'2026-10-28',30,302,330,698)
        r=accounting.reconcile_period(a,b,batches);self.assertEqual([],r['issues']);self.assertEqual('28.00',r['returns']['accountTotalPnl'])
    def test_unknown_fee_or_credit_date_does_not_become_zero_or_today(self):
        row=trade('B1','买入',50,12,1);row['总费用']='';row['资金到账日']=''
        a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch([row]));codes={x['code'] for x in r['issues']}
        self.assertTrue({'FEE_UNKNOWN','CASH_DATE_UNKNOWN'}<=codes);self.assertIsNone(r['returns']['accountTotalPnl']);self.assertIsNone(r['positions'][0]['acquisitionCost'])
    def test_cash_posted_on_cash_date_not_execution_date(self):
        row=trade('B1','买入',10,10,1);row['资金到账日']='2026-10-28';row['结算日']='2026-10-28'
        batches=self.batch([row]);a=statement(self.broker,'2026-10-26',0,0,0,1000);b=statement(self.broker,'2026-10-27',10,101,100,1000)
        r=accounting.reconcile_period(a,b,batches);self.assertEqual('0.00',r['cash']['postedNetChange']);self.assertEqual(1,r['cash']['pendingDatedPostings']);self.assertIsNone(r['returns']['accountTotalPnl'])
    def test_execution_plus_linked_cash_statement_is_counted_once(self):
        row=trade('B1','买入',10,10,1);row['资金到账日']='';self.batch([row],source_role='executions')
        cash=event('C1','买入',成交金额=101,资金发生额=-101,关联流水号='B1');batches=self.batch([cash],source_role='cash')
        a=statement(self.broker,'2026-10-26',0,0,0,1000);b=statement(self.broker,'2026-10-28',10,101,110,899)
        r=accounting.reconcile_period(a,b,batches);self.assertEqual([],r['issues']);self.assertEqual('9.00',r['returns']['accountTotalPnl'])
    def test_missing_period_and_mismatch_remain_visible(self):
        a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch(self.full(),complete=False))
        self.assertIn('COVERAGE_GAP',{i['code'] for i in r['issues']});self.assertIsNone(r['returns']['accountTotalPnl'])
        b['positions'][0]['quantity']=121;r=accounting.reconcile_period(a,b,ledger.load_batches(self.root))
        self.assertEqual(1,r['positions'][0]['quantityDifference']);self.assertIsNone(r['returns']['accountTotalPnl'])
    def test_receivable_difference_and_possible_double_tax_block_total(self):
        a,b=self.pair();rows=self.full();rows[2]['成交金额']=21
        rows.append(event('TAX2','红利税',when='2026-10-28',成交金额=2,资金发生额=-2,关联流水号='DIV-1'))
        r=accounting.reconcile_period(a,b,self.batch(rows));codes={i['code'] for i in r['issues']}
        self.assertTrue({'DIVIDEND_RECEIVABLE_DIFFERENCE','POSSIBLE_DOUBLE_FEE'}<=codes);self.assertIsNone(r['returns']['accountTotalPnl'])
    def test_explicit_included_tax_is_not_debited_twice(self):
        rows=self.full()+[event('TAX2','红利税',when='2026-10-28',成交金额=2,费用已含流水号='D1')]
        a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch(rows));self.assertEqual([],r['issues']);self.assertEqual('483.00',r['returns']['accountTotalPnl'])
    def test_ambiguous_intraday_buy_sell_does_not_invent_fifo_order(self):
        rows=[trade('ZZ','买入',50,12,1),trade('AA','卖出',30,13,1)]
        a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch(rows))
        self.assertIn('INTRADAY_ORDER_UNKNOWN',{i['code'] for i in r['issues']});self.assertIsNone(r['positions'][0]['realizedPnl']);self.assertIsNone(r['returns']['accountTotalPnl'])
    def test_actual_execution_time_orders_partial_buys_and_sales(self):
        rows=self.full();rows[0]['成交时间']='093000';rows[1]=trade('S1','卖出',30,13,1);rows[1]['成交时间']='10:30:00'
        a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch(rows));self.assertEqual([],r['issues']);self.assertEqual('68.80',r['positions'][0]['realizedPnl']);self.assertEqual('483.00',r['returns']['accountTotalPnl'])
    def test_bad_dates_fees_unknown_business_and_missing_id_are_rejected(self):
        variations=[]
        for field,value in [('结算日','2026-10-25'),('资金发生额','-999'),('操作','未知券商特殊业务'),('成交编号',''),('成交数量','1.5')]:
            r=trade('B1','买入',10,10,1);r[field]=value;variations.append(r)
        for row in variations:
            with self.subTest(row=row),self.assertRaises(ValueError):self.batch([row])
    def test_brokers_are_separate_and_merged_requires_matching_period(self):
        a,b=self.pair();self.batch(self.full());statements.confirm(a,self.root,True);statements.confirm(b,self.root,True)
        r=accounting.overview(self.root);self.assertEqual('incomplete-or-different-periods',r['combined']['status']);self.assertIsNone(r['combined']['accountTotalPnl'])
        self.broker='银河证券';self.batch(self.full());a,b=self.pair();statements.confirm(a,self.root,True);statements.confirm(b,self.root,True)
        self.assertEqual('966.00',accounting.overview(self.root)['combined']['accountTotalPnl'])
    def test_plain_source_rows_do_not_retain_identity_columns(self):
        raw=raw_events([trade('B1','买入',10,10,1)]).decode().replace('成交编号,','姓名,资金账号,成交编号,').replace('B1,','FICTIONAL-PERSON,FICTIONAL-ACCOUNT,B1,')
        p=ledger.parse_events(raw.encode(),'.csv',broker=self.broker,coverage_start='2026-10-27',coverage_end='2026-10-28');self.assertNotIn('FICTIONAL-PERSON',json.dumps(p));self.assertNotIn('FICTIONAL-ACCOUNT',json.dumps(p))
    def test_original_v1_batch_remains_readable_and_byte_identical(self):
        self.batch([trade('OLD1','买入',10,10,1)]);p=next(ledger.store(self.root).glob('*.json'));doc=json.loads(p.read_text(encoding='utf8'))
        doc['version']=1
        for k in ['adapter','sourceRole','cashSignConvention','fieldMapping']:doc.pop(k,None)
        for e in doc['events']:
            for k in ['tradeDate','executionDate','settlementDate','cashDate','sourceRole','linkId','includedInEventId','positionDelta','costDelta']:e.pop(k,None)
        doc['sha256']=ledger.digest({k:v for k,v in doc.items() if k not in {'sha256','batchId','confirmedAt'}});p.write_text(json.dumps(doc),encoding='utf8');before=p.read_bytes()
        self.assertEqual(1,ledger.load_batches(self.root)[0]['version']);backup.export_state(self.root);self.assertEqual(before,p.read_bytes())
    def test_unknown_account_scope_cannot_be_declared_total_return(self):
        a,b=self.pair();a['completeAccountDeclared']=False;r=accounting.reconcile_period(a,b,self.batch(self.full()))
        self.assertIn('ACCOUNT_SCOPE_UNCONFIRMED',{i['code'] for i in r['issues']});self.assertIsNone(r['returns']['accountTotalPnl'])
    def test_broker_realized_pnl_conflict_is_preserved_not_fitted(self):
        rows=self.full();rows[1]['已实现盈亏']='80';a,b=self.pair();r=accounting.reconcile_period(a,b,self.batch(rows))
        self.assertEqual('80.00',r['positions'][0]['brokerReportedRealizedPnl']);self.assertEqual('68.80',r['positions'][0]['realizedPnl']);self.assertIn('REALIZED_PNL_DIFFERENCE',{i['code'] for i in r['issues']})

class RecoveryTests(TrustTests):
    def seed(self):
        self.batch(self.full());a,b=self.pair();statements.confirm(a,self.root,True);statements.confirm(b,self.root,True)
        preview=parse_manual([{'symbol':'600900','quantity':100}],as_of='2026-10-26',broker=self.broker)
        commit_preview(preview,self.root/'investment/work/private/holdings',True)
    def target(self):return self.root/'recovery-test-copy'
    def test_backup_restore_recheck_and_second_restore_are_identical(self):
        self.seed();source=backup.export_state(self.root);before=accounting.overview(self.root);target=self.target()
        plan=backup.preview_restore(target,source);self.assertEqual([],plan['conflicts']);self.assertFalse((target/'investment/work/private/recovered-ledgers').exists())
        result=backup.restore(target,plan,True);self.assertEqual(len(source['files']),result['restoredFiles']);self.assertEqual(before,accounting.overview(target))
        after=backup.export_state(target);self.assertEqual(source['manifestSha256'],after['manifestSha256']);self.assertEqual('already-present',backup.restore(target,backup.preview_restore(target,source),True)['status'])
        self.assertEqual(1,len(load_latest_by_broker(target/'investment/work/private/holdings')))
    def test_tampering_path_secret_scope_and_unsupported_version_fail_before_write(self):
        self.seed();source=backup.export_state(self.root)
        for mutate in [lambda x:x.update(version=99),lambda x:x['files'][0].update(name='../escape.json'),lambda x:x['files'][0].update(category='notifications'),lambda x:x['files'][0].update(content='e30=')]:
            bad=deepcopy(source);mutate(bad)
            with self.assertRaises(ValueError):backup.preview_restore(self.target(),bad)
        self.assertFalse((self.target()/'investment/work/private/recovered-ledgers').exists())
    def test_failed_write_before_atomic_publication_preserves_valid_target(self):
        self.seed();source=backup.export_state(self.root);target=self.target();before=backup.export_state(target)
        plan=backup.preview_restore(target,source)
        def fail_write():raise OSError('FICTIONAL-DISK-FAILURE')
        with self.assertRaises(OSError):backup.restore(target,plan,True,before_publish=fail_write)
        self.assertEqual(before['manifestSha256'],backup.export_state(target)['manifestSha256']);self.assertEqual([],ledger.load_batches(target))
    def test_target_changed_since_preview_blocks_without_overwrite(self):
        self.seed();source=backup.export_state(self.root);target=self.target();plan=backup.preview_restore(target,source)
        p=parse_manual([{'symbol':'515450','quantity':10}],as_of='2026-10-27',broker='银河证券');commit_preview(p,target/'investment/work/private/holdings',True)
        before=backup.export_state(target)
        with self.assertRaises(ValueError):backup.restore(target,plan,True)
        self.assertEqual(before['manifestSha256'],backup.export_state(target)['manifestSha256'])
    def test_void_history_is_backed_up_and_cannot_silently_void_active_target(self):
        self.seed();source=backup.export_state(self.root);target=self.target();backup.restore(target,backup.preview_restore(target,source),True)
        batch=ledger.load_batches(self.root)[0];ledger.undo(self.root,batch['batchId'],True);voided=backup.export_state(self.root)
        plan=backup.preview_restore(target,voided);self.assertTrue(plan['conflicts'])
        empty=self.root/'empty-copy';backup.restore(empty,backup.preview_restore(empty,voided),True);self.assertEqual([],ledger.load_batches(empty));self.assertEqual(1,len(ledger.load_batches(empty,include_void=True)))
    def test_secrets_controls_and_report_caches_are_excluded(self):
        self.seed();p=self.root/'investment/work/private/notifications';p.mkdir();(p/'bark.local.json').write_text('{"key":"FICTIONAL-SECRET"}')
        state=backup.export_state(self.root);self.assertNotIn('FICTIONAL-SECRET',json.dumps(state));self.assertNotIn('notifications',{f['category'] for f in state['files']})
    def test_incoming_snapshot_cannot_silently_replace_effective_existing_holdings(self):
        self.seed();source=backup.export_state(self.root);target=self.target()
        old=parse_manual([{'symbol':'600900','quantity':50}],as_of='2026-10-25',broker=self.broker)
        result=commit_preview(old,target/'investment/work/private/holdings',True)
        # The source's original snapshot must sort newer for this regression.
        current=Path(result['path'])
        current.rename(current.with_name('20000101T000000000000-'+current.stem.split('-')[-1]+'.json'))
        doc=json.loads(next((target/'investment/work/private/holdings').glob('*.json')).read_text(encoding='utf8'));doc['snapshotId']=next((target/'investment/work/private/holdings').glob('*.json')).stem
        next((target/'investment/work/private/holdings').glob('*.json')).write_text(json.dumps(doc),encoding='utf8')
        plan=backup.preview_restore(target,source);self.assertTrue(any(c['category']=='holdings' for c in plan['conflicts']))

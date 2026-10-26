import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyState, saveAccount } from '../web/domain.js';
import {
  emptyPlanning, localToday, validatePlanning, saveSnapshot, voidSnapshot, latestSnapshot,
  snapshotTotals, calculateAllocation, saveSalaryPlan, confirmSalaryReceipt,
  addWatch, removeWatch, saveReport, reportAssessment, buildReviewPackage, buildDailyDecision,
} from '../web/planning.js';

const copy = value => structuredClone(value);
const today = () => localToday();
const days = (date, delta) => {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
};
const item = (id, type, balance, extras = {}) => ({ id, name: `虚构${id}`, type, balance, annualRate: null, dueDate: null, note: '', ...extras });
const snapshot = (state = emptyState(), date = today(), items = [item('cash-one', 'bank', '50000')], extras = {}) => saveSnapshot(state, { date, items, ...extras });
const conditions = (extras = {}) => ({
  receipt: '100000', cashBefore: '50000', emergency: '20000', fixed: '30000', future: '10000',
  investment: '25000', savings: '20000', free: '5000', repayment: '10000', debtBalance: '40000',
  date: today(), cashAsOf: today(), debtRate: '5', debtDue: days(today(), 14),
  risk: 'balanced', cashBasis: 'before-receipt', ...extras,
});
const plan = (state, input = conditions(), extras = {}) => saveSalaryPlan(state, { date: input.date, input, status: 'confirmed', ...extras });
const report = (extras = {}) => ({
  format: 'goose-investment-report', version: 1, source: '完全虚构量化测试', asOf: days(today(), -1),
  generatedAt: new Date().toISOString(), strategy: '虚构20日规则', symbols: ['600900.SH', '515450.SH'],
  signals: [{ symbol: '600900.SH', stance: 'watch', basis: '虚构测试依据，不代表行情', risk: '样本不足，可能亏损', quality: 'low' }],
  backtest: null, paperTrades: [], notes: '完全虚构测试，不含个人数据', ...extras,
});
const allocationSum = value => Object.values(value).reduce((sum, amount) => sum + BigInt(amount), 0n);

test('snapshot unknown values never become zero or fabricated complete wealth', () => {
  assert.deepEqual(snapshotTotals(null), { assets: null, liabilities: null, netWorth: null, cash: null, unknownAssets: 0, unknownLiabilities: 0 });
  let state = snapshot(emptyState(), today(), [item('bank', 'bank', '10000'), item('broker', 'securities', null), item('card', 'credit', null)]);
  const totals = snapshotTotals(latestSnapshot(state));
  assert.equal(totals.assets, null); assert.equal(totals.liabilities, null); assert.equal(totals.netWorth, null);
  assert.equal(totals.cash, '10000'); assert.equal(totals.unknownAssets, 1); assert.equal(totals.unknownLiabilities, 1);
  state = snapshot(state, today(), [item('bank', 'bank', null)]);
  assert.equal(snapshotTotals(state.planning.snapshots.at(-1)).cash, null);
});

test('manual snapshot totals are independent of optional ledger and reference-only imported holdings', () => {
  let state = saveAccount(emptyState(), { name: '虚构旧账本', type: 'securities', openingBalance: '900000', openingDate: '2026-01-01' });
  state.imports.push({ id: 'fictional-history', valuationIncluded: false, accounts: [{ cash: null }], positions: [{ symbol: '515450.SH', quantity: 7333, broker_cost: null }] });
  state = snapshot(state, today(), [item('bank', 'bank', '50000'), item('broker', 'securities', '80000'), item('loan', 'loan', '10000')]);
  const totals = snapshotTotals(latestSnapshot(state));
  assert.equal(totals.assets, '130000'); assert.equal(totals.liabilities, '10000'); assert.equal(totals.netWorth, '120000');
  assert.equal(state.accounts[0].openingBalance, '900000');
  assert.equal(state.imports[0].positions[0].quantity, 7333);
  assert.equal(state.imports[0].positions[0].broker_cost, null);
});

test('snapshot revisions preserve original records and historical as-of selection', () => {
  const yesterday = days(today(), -1);
  let state = snapshot(emptyState(), yesterday, [item('bank', 'bank', '100')]);
  const originalId = state.planning.snapshots[0].id;
  state = snapshot(state, today(), [item('bank', 'bank', '200')], { replacesId: originalId, note: '虚构修订' });
  const revisedId = state.planning.snapshots[1].id;
  assert.equal(state.planning.snapshots.length, 2);
  assert.equal(state.planning.snapshots[0].items[0].balance, '100');
  assert.equal(latestSnapshot(state, yesterday).id, originalId);
  assert.equal(latestSnapshot(state, today()).id, revisedId);
  state = voidSnapshot(state, revisedId);
  assert.equal(latestSnapshot(state).id, originalId);
  assert.equal(state.planning.snapshots[1].voided, true);
});

test('same-millisecond snapshot saves select the last appended record, not stale first', () => {
  let state = snapshot(emptyState(), today(), [item('bank', 'bank', '100')]);
  state = snapshot(state, today(), [item('bank', 'bank', '200')]);
  state.planning.snapshots[1].createdAt = state.planning.snapshots[0].createdAt;
  assert.equal(snapshotTotals(latestSnapshot(state)).assets, '200');
});

test('restored snapshot chains reject cycles, backwards dates, orphan references and invalid dates', () => {
  let state = snapshot(emptyState(), days(today(), -1));
  const oldId = state.planning.snapshots[0].id;
  state = snapshot(state, today(), undefined, { replacesId: oldId });
  const mutate = action => { const value = copy(state.planning); action(value); assert.throws(() => validatePlanning(value)); };
  mutate(value => value.snapshots[0].replacesId = value.snapshots[1].id);
  mutate(value => value.snapshots[1].date = days(today(), -2));
  mutate(value => value.snapshots[0].replacesId = 'missing');
  mutate(value => value.snapshots[0].date = '2026-02-30');
  mutate(value => value.snapshots[0].items[0].balance = 0);
  mutate(value => value.snapshots[0].items[0].balance = '-1');
  assert.throws(() => snapshot(emptyState(), days(today(), 1)));
});

test('salary allocations use exact integer cents, remain within available money, and do not imply execution', () => {
  const proposal = calculateAllocation(conditions());
  assert.equal(proposal.available, '150000'); assert.equal(proposal.canConfirm, true);
  assert.deepEqual(proposal.allocations, { cash: '90000', debt: '10000', investment: '25000', savings: '20000', free: '5000' });
  assert.equal(allocationSum(proposal.allocations), 150000n);
  assert.ok(proposal.warnings.some(value => value.includes('未执行')));
  const large = calculateAllocation(conditions({ receipt: '900719925474099301', cashBefore: '20', emergency: '1', fixed: '2', future: '3', repayment: '4', debtBalance: '4', investment: '5', savings: '6', free: '7' }));
  assert.equal(large.available, '900719925474099321');
  assert.equal(allocationSum(large.allocations), 900719925474099321n);
  assert.equal(large.allocations.debt, '4');
});

test('allocation caps debt at known balance then caps remaining priorities without overspending', () => {
  const proposal = calculateAllocation(conditions({ receipt: '100', cashBefore: '0', emergency: '70', fixed: '10', future: '10', repayment: '80', debtBalance: '50', investment: '100', savings: '20', free: '30' }));
  assert.equal(allocationSum(proposal.allocations), 100n);
  assert.deepEqual(proposal.allocations, { cash: '90', debt: '10', investment: '0', savings: '0', free: '0' });
  assert.ok(proposal.warnings.some(value => value.includes('封顶')));
  assert.ok(proposal.warnings.some(value => value.includes('资金不足')));
});

test('unknown cash exposes only known minimum and cannot be confirmed or treated as full available balance', () => {
  const input = conditions({ cashBefore: null });
  const proposal = calculateAllocation(input);
  assert.equal(proposal.available, null);
  assert.equal(proposal.knownMinimum, input.receipt);
  assert.equal(proposal.budgetCap, input.receipt);
  assert.equal(proposal.canConfirm, false);
  assert.equal(proposal.allocations.investment, '0'); assert.equal(proposal.allocations.debt, '0');
  assert.ok(proposal.warnings.some(value => value.includes('未知')));
  assert.throws(() => plan(emptyState(), input));
  assert.doesNotThrow(() => plan(emptyState(), input, { status: 'draft' }));
});

test('unknown obligations and stale cash prevent confirmation; overdue known debt has an explicit warning', () => {
  for (const extras of [{ debtBalance: null }, { debtRate: null }, { debtDue: null }, { emergency: null }, { cashAsOf: days(today(), -1) }]) {
    const input = conditions(extras), proposal = calculateAllocation(input);
    assert.equal(proposal.canConfirm, false);
    assert.equal(proposal.allocations.investment, '0');
    assert.throws(() => plan(emptyState(), input));
  }
  const overdue = calculateAllocation(conditions({ debtDue: days(today(), -1) }));
  assert.ok(overdue.warnings.some(value => /逾期|已过|已到期/.test(value)));
  for (const bad of ['100.1', '-1', 'NaN']) assert.throws(() => calculateAllocation(conditions({ debtRate: bad })));
});

test('confirmed historical plans remain restorable while new historical confirmation is refused', () => {
  let state = plan(emptyState());
  const stored = state.planning.salaryPlans[0];
  const yesterday = days(today(), -1);
  stored.date = yesterday; stored.input.date = yesterday; stored.input.cashAsOf = yesterday;
  assert.doesNotThrow(() => validatePlanning(state.planning));
  assert.throws(() => plan(emptyState(), conditions({ date: yesterday, cashAsOf: yesterday })));
  const invalid = copy(state.planning);
  invalid.salaryPlans[0].input.cashBefore = null;
  assert.throws(() => validatePlanning(invalid));
});

test('restored confirmed allocations cannot bypass essential cash floor or known debt ceiling', () => {
  const state = plan(emptyState());
  const underReserve = copy(state.planning); underReserve.salaryPlans[0].allocations.cash = '1';
  assert.throws(() => validatePlanning(underReserve));
  const aboveDebt = copy(state.planning);
  aboveDebt.salaryPlans[0].allocations = { cash: '60000', debt: '40001', investment: '0', savings: '0', free: '0' };
  assert.throws(() => validatePlanning(aboveDebt));
  assert.throws(() => plan(emptyState(), conditions(), { allocations: { cash: '150001', debt: '0', investment: '0', savings: '0', free: '0' } }));
});

test('salary receipt requires explicit confirmed plan, fresh matching snapshot and known cash target', () => {
  let state = snapshot();
  const snap = latestSnapshot(state), input = conditions();
  state = plan(state, input, { status: 'draft' });
  const draft = state.planning.salaryPlans[0];
  assert.throws(() => confirmSalaryReceipt(state, draft.id, { snapshotId: snap.id, cashItemId: 'cash-one' }));
  state = plan(state, input);
  const confirmed = state.planning.salaryPlans.at(-1);
  assert.throws(() => confirmSalaryReceipt(state, confirmed.id, { snapshotId: 'wrong', cashItemId: 'cash-one' }));
  assert.throws(() => confirmSalaryReceipt(state, confirmed.id, { snapshotId: snap.id, cashItemId: 'missing' }));
  let wrongBalance = snapshot(emptyState(), today(), [item('cash-one', 'bank', '1')]);
  wrongBalance = plan(wrongBalance);
  assert.throws(() => confirmSalaryReceipt(wrongBalance, wrongBalance.planning.salaryPlans[0].id, { snapshotId: latestSnapshot(wrongBalance).id, cashItemId: 'cash-one' }));
});

test('confirmed receipt adds only actual receipt and never performs allocation, debt payment or investment', () => {
  const items = [item('bank', 'bank', '30000'), item('cash', 'cash', '20000'), item('loan', 'loan', '40000'), item('broker', 'securities', null)];
  let state = snapshot(emptyState(), today(), items);
  state = plan(state);
  const before = copy(state), oldSnapshot = latestSnapshot(state), planId = state.planning.salaryPlans[0].id;
  const applied = confirmSalaryReceipt(state, planId, { snapshotId: oldSnapshot.id, cashItemId: 'bank' });
  assert.deepEqual(state, before);
  const newest = latestSnapshot(applied);
  assert.equal(newest.replacesId, oldSnapshot.id);
  assert.equal(newest.items.find(value => value.id === 'bank').balance, '130000');
  assert.equal(newest.items.find(value => value.id === 'cash').balance, '20000');
  assert.equal(newest.items.find(value => value.id === 'loan').balance, '40000');
  assert.equal(newest.items.find(value => value.id === 'broker').balance, null);
  assert.equal(snapshotTotals(newest).cash, '150000');
  assert.equal(applied.entries.length, 0);
  assert.equal(applied.planning.salaryPlans[0].receiptApplied.snapshotId, newest.id);
  assert.throws(() => confirmSalaryReceipt(applied, planId, { snapshotId: newest.id, cashItemId: 'bank' }), /入账|确认/);
  assert.throws(() => voidSnapshot(applied, newest.id), /关联|工资/);
  assert.throws(() => plan(applied, conditions(), { id: planId }));
});

test('report import preserves source dates, rejects duplicates and warns clearly about stale claims', () => {
  const input = report();
  let state = saveReport(emptyState(), input);
  const saved = state.planning.reports[0];
  assert.equal(saved.source, input.source); assert.equal(saved.asOf, input.asOf); assert.equal(saved.generatedAt, input.generatedAt);
  assert.throws(() => saveReport(state, input));
  assert.throws(() => saveReport(state, { ...input, id: 'changed-report-id', notes: '同来源、同数据日期、同策略，不应静默新增第二份', generatedAt: new Date(Date.now() - 1000).toISOString() }));
  const restored = copy(state.planning);
  restored.reports.push({ ...copy(saved), id: 'duplicate-by-source-date', notes: '另一内容' });
  assert.throws(() => validatePlanning(restored));
  const stale = reportAssessment(saved, days(saved.asOf, 4));
  assert.equal(stale.stale, true);
  assert.ok(stale.warnings.some(value => value.includes('历史')));
  assert.ok(stale.warnings.some(value => value.includes('未由本应用独立复算')));
  assert.ok(stale.warnings.some(value => value.includes('缺少回测')));
});

test('unsupported report/planning schemas and unverified arithmetic inputs are refused', () => {
  assert.throws(() => validatePlanning({ ...emptyPlanning(), schemaVersion: 99 }));
  for (const patch of [
    { version: 99 }, { format: 'arbitrary-json' }, { source: '' }, { strategy: '' },
    { asOf: days(today(), 1) }, { asOf: '2026-02-30' }, { generatedAt: new Date(Date.now() + 86400000).toISOString() }, { symbols: ['600900.SH', '600900.SH'] },
    { paperTrades: [{ date: today(), symbol: '600900.SH', side: 'buy', quantity: 1, price: '0.1', totalCost: '1' }] },
    { backtest: { from: '2026-01-01', to: days(today(), 1), returnBps: 1, maxDrawdownBps: 1, commissionBps: 1, slippageBps: 1, tradeCount: 1, limitations: '' } },
  ]) assert.throws(() => saveReport(emptyState(), report(patch)));
  const unsafe = JSON.parse('{"schemaVersion":1,"snapshots":[],"reports":[],"watchlist":[],"salaryPlans":[],"__proto__":{"polluted":true}}');
  assert.throws(() => validatePlanning(unsafe));
  assert.equal({}.polluted, undefined);
});

test('watchlist is explicit, duplicate safe, and never creates valuation or transactions', () => {
  const state = addWatch(emptyState(), { symbol: '515450.SH', name: '虚构测试关注' });
  assert.equal(state.planning.watchlist.length, 1); assert.equal(state.entries.length, 0); assert.equal(state.accounts.length, 0);
  assert.throws(() => addWatch(state, { symbol: '515450.SH' }));
  assert.throws(() => addWatch(state, { symbol: '__proto__' }));
  assert.equal(removeWatch(state, state.planning.watchlist[0].id).planning.watchlist.length, 0);
  assert.equal(state.planning.watchlist.length, 1);
});

test('AI review export separates dated source facts from empty inference/suggestion sections and performs no upload', () => {
  let state = snapshot(emptyState(), today(), [item('broker', 'securities', null)]);
  state = saveReport(state, report());
  state = addWatch(state, { symbol: '515450.SH', name: '虚构关注' });
  state.imports.push({ id: 'fictional-import', source: '虚构历史导入', asOf: days(today(), -5), valuationIncluded: false, positions: [{ symbol: '515450.SH', quantity: 7333, broker_cost: null }] });
  const original = copy(state);
  const pkg = buildReviewPackage(state);
  assert.equal(pkg.format, 'goose-ai-review-input'); assert.equal(pkg.version, 1); assert.equal(pkg.asOf, today());
  assert.equal(pkg.facts.snapshot.date, today()); assert.equal(pkg.facts.totals.assets, null);
  assert.equal(pkg.facts.historicalPositions[0].valuationIncluded, false);
  assert.equal(pkg.facts.reports[0].source, '完全虚构量化测试');
  assert.deepEqual(pkg.modelInferences, []); assert.deepEqual(pkg.actionSuggestions, []);
  assert.ok(pkg.privacy.includes('未发送'));
  assert.ok(pkg.constraints.some(value => value.includes('模型推测不属于回测')));
  assert.deepEqual(state, original);
  pkg.facts.snapshot.items[0].balance = '999';
  assert.equal(latestSnapshot(state).items[0].balance, null);
});

test('historical AI packages exclude reports, imports and salary plans after requested as-of date', () => {
  const yesterday = days(today(), -1);
  let state = snapshot(emptyState(), yesterday, [item('cash', 'bank', '100')]);
  state = snapshot(state, today(), [item('cash', 'bank', '200')]);
  state = saveReport(state, report({ asOf: yesterday }));
  state = saveReport(state, report({ asOf: today() }));
  state = plan(state, conditions({ date: yesterday, cashAsOf: yesterday }), { status: 'draft' });
  state = plan(state, conditions(), { status: 'draft' });
  state.imports.push({ id: 'old-import', asOf: yesterday }, { id: 'new-import', asOf: today() });
  const pkg = buildReviewPackage(state, yesterday);
  assert.equal(pkg.facts.snapshot.date, yesterday);
  assert.equal(pkg.facts.totals.cash, '100');
  assert.equal(pkg.facts.reports.length, 1); assert.equal(pkg.facts.reports[0].asOf, yesterday);
  assert.equal(pkg.facts.salaryPlans.length, 1); assert.equal(pkg.facts.salaryPlans[0].date, yesterday);
  assert.deepEqual(pkg.facts.historicalPositions.map(value => value.id), ['old-import']);
});

test('restored applied salary receipt must reference a real unique nonvoid cash increase rather than arbitrary snapshot', () => {
  let state = snapshot();
  state = plan(state);
  const sourceId = latestSnapshot(state).id;
  state = confirmSalaryReceipt(state, state.planning.salaryPlans[0].id, { snapshotId: sourceId, cashItemId: 'cash-one' });
  const receiptId = state.planning.salaryPlans[0].receiptApplied.snapshotId;
  const invalid = (label, action) => { const p = copy(state.planning); action(p, p.snapshots.find(value => value.id === receiptId)); assert.throws(() => validatePlanning(p), label); };
  invalid('voided receipt snapshot', (_p, s) => s.voided = true);
  invalid('wrong receipt date', (_p, s) => s.date = days(today(), -1));
  invalid('receipt points at debt account', (_p, s) => s.items[0].type = 'loan');
  invalid('receipt cash balance is unknown', (_p, s) => s.items[0].balance = null);
  invalid('cash increase differs from receipt', (_p, s) => s.items[0].balance = '150001');
  invalid('receipt has no before snapshot', (_p, s) => s.replacesId = null);
  invalid('same receipt claimed by two plans', p => p.salaryPlans.push({ ...copy(p.salaryPlans[0]), id: 'duplicate-receipt-owner' }));
  assert.doesNotThrow(() => validatePlanning(state.planning));
});

test('saving snapshots and salary budgets rejects 61-digit aggregate cents before display or persistence', () => {
  const max = '9'.repeat(60);
  for (const types of [['bank', 'securities'], ['loan', 'credit'], ['bank', 'cash']]) {
    assert.throws(() => snapshot(emptyState(), today(), [item('large-one', types[0], max), item('large-two', types[1], '1')]), /金额/);
  }
  const boundary = snapshot(emptyState(), today(), [item('asset', 'bank', max), item('debt', 'loan', max)]);
  assert.equal(snapshotTotals(latestSnapshot(boundary)).netWorth, '0');
  assert.throws(() => calculateAllocation(conditions({ receipt: max, cashBefore: '1' })), /金额/);
  const atBoundary = calculateAllocation(conditions({ receipt: max, cashBefore: '0' }));
  assert.equal(atBoundary.available, max);
  assert.equal(allocationSum(atBoundary.allocations), BigInt(max));
});

test('daily decisions distinguish missing, historical and source-unverified reports without inventing prices', () => {
  const empty = buildDailyDecision(emptyState());
  assert.equal(empty.format, 'goose-daily-decision'); assert.equal(empty.date, today());
  assert.equal(empty.status, 'missing'); assert.equal(empty.sourceReport, null);
  assert.equal(empty.facts.snapshotDate, null); assert.equal(empty.facts.netWorth, null);
  assert.equal(empty.facts.signalCount, 0); assert.equal(empty.facts.backtestAvailable, false);
  assert.ok(empty.warnings.some(value => value.includes('缺少')));
  let state = snapshot(emptyState(), today(), [item('cash', 'bank', '10000'), item('broker', 'securities', null)]);
  state.imports.push({ id: 'fictional-historical', asOf: days(today(), -10), valuationIncluded: false });
  state = saveReport(state, report({ asOf: days(today(), -4) }));
  const historical = buildDailyDecision(state);
  assert.equal(historical.status, 'historical'); assert.equal(historical.sourceReport.asOf, days(today(), -4));
  assert.equal(historical.facts.snapshotDate, today()); assert.equal(historical.facts.netWorth, null);
  assert.deepEqual(historical.facts.positionsAsOf, [days(today(), -10)]);
  assert.ok(historical.warnings.some(value => value.includes('历史')));
  state = saveReport(state, report({ asOf: today() }));
  const current = buildDailyDecision(state);
  assert.equal(current.status, 'source-unverified');
  assert.equal(current.sourceReport.source, '完全虚构量化测试'); assert.equal(current.sourceReport.asOf, today());
  assert.equal(current.facts.signalCount, 1); assert.equal(current.facts.paperTradeCount, 0);
  assert.ok(current.action.includes('不执行交易'));
  const verifyNoPriceFields = value => {
    if (!value || typeof value !== 'object') return;
    for (const [key, child] of Object.entries(value)) {
      assert.ok(!/price|quote|marketValue|currentValue/i.test(key), `unexpected invented market field: ${key}`);
      verifyNoPriceFields(child);
    }
  };
  for (const value of [empty, historical, current]) verifyNoPriceFields(value);
});

test('1000 valid snapshot revisions validate promptly and a tail cycle is rejected', { timeout: 10000 }, () => {
  const p = emptyPlanning(), createdAt = new Date().toISOString();
  p.snapshots = Array.from({ length: 1000 }, (_, index) => ({
    id: `chain-${index}`, date: today(), items: [item('cash', 'bank', String(index))], note: '虚构长修订链',
    createdAt, voided: false, replacesId: index === 0 ? null : `chain-${index - 1}`,
  }));
  const start = performance.now();
  assert.equal(validatePlanning(p), p);
  assert.ok(performance.now() - start < 2000, '1000 revisions must not regress to repeated cubic ancestor scans');
  p.snapshots[0].replacesId = 'chain-999';
  assert.throws(() => validatePlanning(p), /循环/);
});

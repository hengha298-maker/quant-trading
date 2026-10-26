import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyState } from '../web/domain.js';
import { saveReport, validatePlanning, researchComparison, reportAssessment, buildDailyDecision, buildReviewPackage } from '../web/planning.js';
import { encryptBackup, decryptBackup, migrateState, canonical } from '../web/io.js';

const copy = value => structuredClone(value);
const legacy = () => ({
  format: 'goose-investment-report', version: 1, source: '完全虚构兼容报告',
  asOf: '2025-01-31', generatedAt: '2026-01-01T00:00:00.000Z',
  strategy: '仅测试旧版导入', symbols: ['600900.SH'],
  signals: [{ symbol: '600900.SH', stance: 'watch', basis: '完全虚构测试，无真实价格', risk: '缺乏真实证据', quality: 'low' }],
  backtest: { from: '2024-02-01', to: '2025-01-31', returnBps: 1200, maxDrawdownBps: 900, commissionBps: 3, slippageBps: 5, tradeCount: 2, limitations: '虚构测试，不代表真实策略收益' },
  paperTrades: [], notes: '完全虚构的自动测试，不读取私人或真实行情数据',
});
const extension = () => ({
  version: 1,
  dataset: { source: '完全虚构的固定测试数据', sourceUrl: 'https://example.invalid/goose-synthetic', through: '2025-01-31', adjustment: 'none', priceUnit: 'CNY-per-share', quality: 'synthetic', crossValidated: false, unitVerified: true, sha256: 'a'.repeat(64) },
  comparison: [
    { strategy: 'buy_hold', totalReturnBps: 1200, annualizedReturnBps: 1220, maxDrawdownBps: 900, volatilityBps: 1800, tradeCount: 2, cost: '100' },
    { strategy: 'v1_ma20', totalReturnBps: 1400, annualizedReturnBps: 1420, maxDrawdownBps: 700, volatilityBps: 1600, tradeCount: 8, cost: '244' },
    { strategy: 'ma20_cross', totalReturnBps: 900, annualizedReturnBps: 920, maxDrawdownBps: 750, volatilityBps: 1500, tradeCount: 6, cost: '153' },
    { strategy: 'periodic', totalReturnBps: 800, annualizedReturnBps: 820, maxDrawdownBps: 800, volatilityBps: 1400, tradeCount: 12, cost: '60' },
  ],
  split: { train: { from: '2024-02-01', to: '2024-08-30' }, validation: { from: '2024-09-02', to: '2024-11-29' }, test: { from: '2024-12-02', to: '2025-01-31' } },
  limitations: ['全部指标完全虚构，仅用于验证数据结构', '价格研究未包含真实分红、税费与市场冲击'],
  reportKind: 'historical-research', generatedBeforeCutoff: false, decisionAllowed: false,
  health: { status: 'synthetic', codes: ['SYNTHETIC_TEST_ONLY', 'NOT_CROSS_VALIDATED'] },
});
const researchReport = () => ({ ...legacy(), research: extension() });
function accepted(doc = researchReport()) { return saveReport(emptyState(), doc); }
function rejected(change) { const doc = researchReport(); change(doc); assert.throws(() => accepted(doc)); }

test('old version 1 reports without research remain valid and do not gain invented provenance', () => {
  const state = accepted(legacy());
  assert.equal(state.planning.reports[0].version, 1);
  assert.ok(!Object.hasOwn(state.planning.reports[0], 'research'));
  assert.deepEqual(researchComparison(state.planning.reports[0]), []);
  assert.ok(reportAssessment(state.planning.reports[0]).warnings.some(value => value.includes('旧版报告')));
  assert.deepEqual(migrateState(state), state);
});

test('optional research survives migration and complete encrypted export/restore without field loss', async () => {
  const state = accepted();
  assert.equal(validatePlanning(state.planning), state.planning);
  const restored = await decryptBackup(await encryptBackup(state, 'fictional-research-backup-password'), 'fictional-research-backup-password');
  assert.equal(canonical(restored), canonical(state));
  assert.equal(restored.planning.reports[0].research.dataset.priceUnit, 'CNY-per-share');
  assert.deepEqual(restored.planning.reports[0].research.split, extension().split);
});

test('four-strategy cost differences use exact integer cents even beyond floating point safe range', () => {
  const doc = researchReport();
  doc.research.comparison[0].cost = '900719925474099312345';
  doc.research.comparison[1].cost = '900719925474099312346';
  const comparison = researchComparison(accepted(doc).planning.reports[0]);
  assert.equal(comparison[1].costDifference, '1');
  assert.equal(comparison[1].excessReturnBps, 200);
  assert.equal(comparison[3].costDifference, '-900719925474099312285');
  assert.deepEqual(comparison.map(value => value.strategy), ['buy_hold', 'v1_ma20', 'ma20_cross', 'periodic']);
});

test('research requires exact schema, decision prohibition, complete unique strategies and integer metrics', () => {
  rejected(doc => doc.research = null);
  rejected(doc => doc.research.version = 2);
  rejected(doc => doc.research.decisionAllowed = true);
  rejected(doc => delete doc.research.decisionAllowed);
  rejected(doc => doc.research.fakeVerified = true);
  rejected(doc => doc.research.comparison.pop());
  rejected(doc => doc.research.comparison[3].strategy = 'buy_hold');
  rejected(doc => doc.research.comparison[0].tradeCount = 0.5);
  rejected(doc => doc.research.comparison[0].maxDrawdownBps = 10001);
  rejected(doc => doc.research.comparison[0].totalReturnBps = -10001);
  rejected(doc => doc.research.comparison[0].cost = '1.01');
  rejected(doc => doc.research.comparison[0].cost = 101);
  rejected(doc => doc.research.limitations = []);
});

test('training validation and test ranges cannot overlap, reverse or exceed data/report cutoff', () => {
  rejected(doc => doc.research.split.validation.from = '2024-08-30');
  rejected(doc => doc.research.split.test.from = '2025-02-01');
  rejected(doc => doc.research.split.train.to = '2024-02-30');
  rejected(doc => doc.research.split.test.to = '2025-02-01');
  rejected(doc => doc.research.dataset.through = '2025-02-01');
  rejected(doc => doc.generatedAt = '2025-01-30T00:00:00.000Z');
  rejected(doc => doc.backtest.from = '2024-01-01');
  rejected(doc => doc.backtest.to = '2024-12-31');
});

test('fake units, prices, provenance credentials and false high-confidence claims are rejected', () => {
  rejected(doc => doc.research.dataset.sourceUrl = 'javascript:alert(1)');
  rejected(doc => doc.research.dataset.sourceUrl = 'https://user:secret@example.com/data');
  rejected(doc => doc.research.dataset.sourceUrl = 'https://example.invalid/\nsynthetic');
  rejected(doc => doc.research.dataset.source = '');
  rejected(doc => doc.research.dataset.sha256 = 'not-a-file-digest');
  rejected(doc => doc.research.dataset.priceUnit = 'fen-per-share');
  rejected(doc => doc.research.dataset.adjustment = 'qfq');
  rejected(doc => doc.research.dataset.crossValidated = true);
  rejected(doc => doc.research.health.status = 'historical');
  rejected(doc => doc.signals[0].quality = 'high');
  rejected(doc => doc.signals[0].quality = 'medium');
  rejected(doc => doc.signals[0].stance = 'accumulate');
  rejected(doc => doc.signals[0].stance = 'hold');
  rejected(doc => doc.paperTrades = [{ date: '2025-01-30', symbol: '600900.SH', side: 'buy', quantity: 100, price: 12.34, totalCost: '123400' }]);
});

test('single-source structurally validated prices never imply independently verified high confidence', () => {
  const doc = researchReport();
  doc.research.dataset = { ...doc.research.dataset, source: '东方财富历史行情（测试来源声明）', sourceUrl: 'https://push2his.eastmoney.com/api/qt/stock/kline/get', quality: 'validated', crossValidated: false };
  doc.research.health = { status: 'historical', codes: ['SINGLE_SOURCE_NOT_CROSS_VALIDATED'] };
  doc.signals[0].quality = 'medium';
  const state = accepted(doc);
  assert.ok(reportAssessment(state.planning.reports[0]).warnings.some(value => value.includes('单一来源')));
  doc.signals[0].quality = 'high'; assert.throws(() => accepted(doc));
  doc.signals[0].quality = 'medium'; doc.research.dataset.unitVerified = false; assert.throws(() => accepted(doc));
  doc.research.dataset.unitVerified = true; doc.research.dataset.sourceUrl = 'https://example.invalid/fake-real-prices'; assert.throws(() => accepted(doc));
});

test('synthetic/blocked historical reports remain observation-only and cannot become today trading decisions', () => {
  const synthetic = accepted();
  const warnings = reportAssessment(synthetic.planning.reports[0], '2025-01-31').warnings;
  assert.ok(warnings.some(value => value.includes('完全虚构')));
  assert.ok(warnings.some(value => value.includes('决策权限已禁用')));
  const daily = buildDailyDecision(synthetic, '2025-01-31');
  assert.equal(daily.status, 'historical');
  assert.equal(daily.action, '先核实数据；本报告不执行交易');
  const blocked = researchReport(); blocked.research.dataset.quality = 'blocked'; blocked.research.dataset.unitVerified = false; blocked.research.dataset.sourceUrl = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'; blocked.research.health = { status: 'blocked', codes: ['PRICE_UNIT_NOT_VERIFIED'] };
  const state = accepted(blocked);
  assert.ok(reportAssessment(state.planning.reports[0]).warnings.some(value => value.includes('阻断')));
});

test('a newly dated report cannot disguise old underlying research data as fresh', () => {
  const doc = researchReport(); doc.asOf = '2026-01-31'; doc.generatedAt = '2026-01-31T00:00:00.000Z';
  const assessment = reportAssessment(accepted(doc).planning.reports[0], '2026-01-31');
  assert.equal(assessment.stale, true);
  assert.ok(assessment.warnings.some(value => value.includes('研究数据实际截止2025-01-31')));
});

test('before-cutoff claims are checked using Asia/Shanghai time and never grant decision permission', () => {
  const before = researchReport(); before.research.reportKind = 'pre1445-test'; before.research.generatedBeforeCutoff = true; before.generatedAt = '2026-01-01T06:44:59.000Z';
  assert.ok(accepted(before));
  const atCutoff = copy(before); atCutoff.generatedAt = '2026-01-01T06:45:00.000Z'; assert.throws(() => accepted(atCutoff));
  const after = copy(before); after.generatedAt = '2026-01-01T07:00:00.000Z'; assert.throws(() => accepted(after));
  after.research.generatedBeforeCutoff = false;
  assert.ok(reportAssessment(accepted(after).planning.reports[0]).warnings.some(value => value.includes('未通过')));
});

test('same report remains deduplicated with research extension and original data stays unchanged on error', () => {
  const doc = researchReport(), state = accepted(doc), before = canonical(state);
  assert.throws(() => saveReport(state, copy(doc)), /同一/);
  const changed = copy(doc); changed.research.dataset.priceUnit = 'fake-price';
  assert.throws(() => saveReport(state, changed));
  assert.equal(canonical(state), before);
});

test('daily decision export distinguishes real research simulation counts from omitted embedded details', () => {
  const state = accepted();
  const daily = buildDailyDecision(state, '2025-01-31');
  assert.equal(daily.facts.paperTradeCount, 8);
  assert.equal(daily.facts.embeddedPaperTradeCount, 0);
  assert.equal(daily.facts.paperTradeCountSource, 'research-simulation');
  assert.equal(daily.status, 'historical');
  const withOneDetail = researchReport();
  withOneDetail.paperTrades = [{ date: '2025-01-30', symbol: '600900.SH', side: 'buy', quantity: 100, price: '100', totalCost: '10100' }];
  const details = buildDailyDecision(accepted(withOneDetail), '2025-01-31');
  assert.equal(details.facts.paperTradeCount, 8);
  assert.equal(details.facts.embeddedPaperTradeCount, 1);
  assert.equal(details.facts.paperTradeCountSource, 'research-simulation');
});

test('legacy and no-report daily exports retain embedded record count semantics', () => {
  const doc = legacy(); doc.paperTrades = [{ date: '2025-01-30', symbol: '600900.SH', side: 'buy', quantity: 100, price: '100', totalCost: '10100' }];
  const daily = buildDailyDecision(accepted(doc), '2025-01-31');
  assert.equal(daily.facts.paperTradeCount, 1);
  assert.equal(daily.facts.embeddedPaperTradeCount, 1);
  assert.equal(daily.facts.paperTradeCountSource, 'embedded-paper-records');
  const missing = buildDailyDecision(emptyState(), '2025-01-31');
  assert.equal(missing.facts.paperTradeCount, 0);
  assert.equal(missing.facts.embeddedPaperTradeCount, 0);
  assert.equal(missing.facts.paperTradeCountSource, 'embedded-paper-records');
});

test('AI review package preserves reported comparison counts and omitted detail arrays without a misleading derived zero', () => {
  const state = accepted(), before = canonical(state);
  const review = buildReviewPackage(state, '2025-01-31');
  const report = review.facts.reports[0];
  assert.equal(report.research.comparison.find(item => item.strategy === 'v1_ma20').tradeCount, 8);
  assert.equal(report.paperTrades.length, 0);
  assert.equal(Object.hasOwn(review.facts, 'paperTradeCount'), false);
  assert.equal(canonical(state), before);
});

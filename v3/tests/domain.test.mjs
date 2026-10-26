import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyState, parseMoney, formatMoney, validateState, saveAccount, archiveAccount, saveEntry, voidEntry, balances, overview } from '../web/domain.js';

const account = (state, type = 'bank', openingBalance = '100000', name = type, openingDate = '2026-01-01') => saveAccount(state, { name, type, openingBalance, openingDate });
const entry = (state, input) => saveEntry(state, { date: '2026-10-01', ...input });
const deep = value => JSON.parse(JSON.stringify(value));

test('decimal parsing is exact, canonical, signed, and never guesses unknown as zero', () => {
  assert.equal(parseMoney('0.10'), '10');
  assert.equal(parseMoney('0.20'), '20');
  assert.equal(parseMoney('00001.5'), '150');
  assert.equal(parseMoney('-.01'), '-1');
  assert.equal(parseMoney('+0.00'), '0');
  assert.equal(parseMoney('9007199254740993.01'), '900719925474099301');
  for (const value of ['', '  ', null, undefined]) assert.equal(parseMoney(value), null);
  for (const value of ['1.001', '1e3', 'NaN', '1,000', '--1', 'Infinity', 0.1]) assert.throws(() => parseMoney(value));
  assert.equal(formatMoney('900719925474099301'), '¥9,007,199,254,740,993.01');
  assert.equal(formatMoney('-1'), '-¥0.01');
  assert.equal(formatMoney(null), '未知');
});

test('empty state has explicit version and zero only for known empty totals', () => {
  assert.equal(validateState(emptyState()).schemaVersion, 1);
  assert.deepEqual(balances(emptyState()), {});
  assert.deepEqual(overview(emptyState(), '2026-10'), { assets: '0', liabilities: '0', netWorth: '0', unknownAssets: 0, unknownLiabilities: 0, income: '0', expense: '0', surplus: '0', history: [] });
});

test('income, expense, month cutoff and chronology use exact cents', () => {
  let state = account(emptyState(), 'bank', '900719925474099300');
  const id = state.accounts[0].id;
  state = entry(state, { kind: 'income', accountId: id, amount: '10' });
  state = entry(state, { kind: 'income', accountId: id, amount: '20' });
  state = entry(state, { kind: 'expense', accountId: id, amount: '1' });
  state = entry(state, { kind: 'expense', accountId: id, amount: '5', date: '2026-11-01' });
  assert.equal(balances(state, '9999-12-31')[id], '900719925474099324');
  const view = overview(state, '2026-10', '2026-10-31');
  assert.equal(view.assets, '900719925474099329');
  assert.equal(view.income, '30'); assert.equal(view.expense, '1'); assert.equal(view.surplus, '29');
  assert.deepEqual(view.history.map(item => item.date), ['2026-01-01', '2026-10-01']);
});

test('unknown assets and debt stay unknown until explicit adjustment; future opening is excluded', () => {
  let state = account(emptyState(), 'bank', null);
  const id = state.accounts[0].id;
  state = account(state, 'loan', null);
  const debt = state.accounts[1].id;
  state = entry(state, { kind: 'income', accountId: id, amount: '100' });
  assert.equal(balances(state)[id], null);
  let view = overview(state, '2026-10');
  assert.equal(view.assets, null); assert.equal(view.liabilities, null); assert.equal(view.netWorth, null);
  assert.equal(view.unknownAssets, 1); assert.equal(view.unknownLiabilities, 1);
  state = entry(state, { kind: 'adjustment', accountId: id, amount: '1000', date: '2026-10-02' });
  state = entry(state, { kind: 'adjustment', accountId: debt, amount: '300', date: '2026-10-02' });
  view = overview(state, '2026-10', '2026-10-02');
  assert.equal(view.netWorth, '700');
  assert.equal(view.history[0].netWorth, null);
  state = account(state, 'other', null, '未来账户', '2027-01-01');
  assert.equal(overview(state, '2026-10', '2026-10-02').netWorth, '700');
});

test('transfers and investment purchases/sales do not duplicate income or total assets', () => {
  let state = account(emptyState(), 'bank', '100000');
  state = account(state, 'cash', '0');
  state = account(state, 'securities', '50000');
  const [bank, cash, stock] = state.accounts.map(value => value.id);
  state = entry(state, { kind: 'transfer', accountId: bank, toAccountId: cash, amount: '20000' });
  state = entry(state, { kind: 'buy', accountId: bank, toAccountId: stock, amount: '10000' });
  state = entry(state, { kind: 'sell', accountId: stock, toAccountId: cash, amount: '3000' });
  assert.deepEqual(balances(state), { [bank]: '70000', [cash]: '23000', [stock]: '57000' });
  assert.equal(overview(state, '2026-10').assets, '150000');
  assert.equal(overview(state, '2026-10').income, '0');
  assert.equal(overview(state, '2026-10').expense, '0');
  state = entry(state, { kind: 'dividend', accountId: bank, amount: '200' });
  assert.equal(overview(state, '2026-10').income, '200');
  assert.equal(overview(state, '2026-10').assets, '150200');
});

test('unknown investment balance is not valued from imported quantities or trade cash', () => {
  let state = account(emptyState(), 'bank', '100000');
  state = account(state, 'securities', null);
  const [bank, stock] = state.accounts.map(value => value.id);
  state.imports.push({ id: 'fictional-import', source: 'fictional-test', positions: [{ symbol: '515450', quantity: '8100' }] });
  state = entry(state, { kind: 'buy', accountId: bank, toAccountId: stock, amount: '1000' });
  assert.equal(balances(state)[stock], null);
  assert.equal(overview(state, '2026-10').assets, null);
  assert.equal(overview(state, '2026-10').income, '0');
});

test('repayment separates principal from expense and enforces historical debt limit', () => {
  let state = account(emptyState(), 'bank', '100000');
  state = account(state, 'credit', '50000');
  const [bank, debt] = state.accounts.map(value => value.id);
  state = entry(state, { kind: 'repayment', accountId: bank, toAccountId: debt, amount: '10200', principal: '10000', interest: '200' });
  assert.deepEqual(balances(state), { [bank]: '89800', [debt]: '40000' });
  const view = overview(state, '2026-10');
  assert.equal(view.expense, '200'); assert.equal(view.surplus, '-200'); assert.equal(view.netWorth, '49800');
  assert.throws(() => entry(state, { kind: 'repayment', accountId: bank, toAccountId: debt, amount: '40001', principal: '40001', interest: '0' }), /超过/);
  assert.throws(() => entry(state, { kind: 'repayment', accountId: bank, toAccountId: debt, amount: '100', principal: '99', interest: '0' }), /本金加利息/);
  assert.throws(() => entry(state, { kind: 'adjustment', accountId: debt, amount: '-1' }));
  state = entry(state, { kind: 'adjustment', accountId: bank, amount: '-100', date: '2026-10-03' });
  assert.equal(balances(state)[bank], '-100');
});

test('edits, voids and archive retain history and before/after audit without mutating callers', () => {
  const original = account(emptyState());
  const id = original.accounts[0].id;
  let state = entry(original, { kind: 'expense', accountId: id, amount: '200' });
  const entryId = state.entries[0].id;
  assert.equal(original.entries.length, 0);
  state = saveEntry(state, { id: entryId, amount: '350' });
  assert.equal(state.audit.at(-1).before.amount, '200');
  assert.equal(state.audit.at(-1).after.amount, '350');
  state = saveAccount(state, { id, name: '新账户名' });
  assert.equal(state.audit.at(-1).before.name, 'bank');
  state = voidEntry(state, entryId);
  assert.equal(balances(state)[id], '100000');
  assert.equal(overview(state, '2026-10').expense, '0');
  assert.equal(state.entries[0].voided, true);
  state = archiveAccount(state, id);
  assert.equal(state.accounts[0].archived, true);
  assert.equal(overview(state, '2026-10').assets, '100000');
  assert.throws(() => entry(state, { kind: 'income', accountId: id, amount: '1' }), /归档/);
  assert.throws(() => saveEntry(state, { id: entryId, amount: '1' }), /撤销/);
  state = saveAccount(state, { id, archived: false });
  assert.equal(state.accounts[0].archived, false);
  assert.equal(state.audit.at(-1).before.archived, true);
  state = entry(state, { kind: 'income', accountId: id, amount: '1' });
  assert.equal(balances(state)[id], '100001');
});

test('adjustments replay chronologically even when entered out of order', () => {
  let state = account(emptyState(), 'cash', '100');
  const id = state.accounts[0].id;
  state = entry(state, { kind: 'income', accountId: id, amount: '30', date: '2026-10-03' });
  state = entry(state, { kind: 'adjustment', accountId: id, amount: '500', date: '2026-10-02' });
  state = entry(state, { kind: 'expense', accountId: id, amount: '20', date: '2026-10-01' });
  assert.equal(balances(state)[id], '530');
  assert.equal(balances(state, '2026-10-01')[id], '80');
  assert.deepEqual(overview(state, '2026-10').history.map(value => value.netWorth), ['100', '80', '500', '530']);
  const reset = state.entries.find(value => value.kind === 'adjustment');
  assert.equal(balances(voidEntry(state, reset.id))[id], '110');
});

test('malicious or invalid imported states fail before use', () => {
  const good = account(emptyState());
  const invalid = [];
  const mutate = action => { const state = deep(good); action(state); invalid.push(state); };
  mutate(state => state.schemaVersion = 99);
  mutate(state => state.accounts.push(deep(state.accounts[0])));
  mutate(state => state.accounts[0].openingBalance = 1);
  mutate(state => state.accounts[0].openingBalance = '01');
  mutate(state => state.accounts[0].openingBalance = '-0');
  mutate(state => state.accounts[0].id = '__proto__');
  mutate(state => state.accounts[0].type = 'broker-password');
  mutate(state => state.accounts[0].openingDate = '2026-02-29');
  mutate(state => state.accounts[0].annualRate = '-1');
  mutate(state => state.accounts[0].archived = 'false');
  mutate(state => state.imports.push(JSON.parse('{"__proto__":{"polluted":true}}')));
  mutate(state => state.audit.push({ value: Infinity }));
  for (const state of invalid) assert.throws(() => validateState(state));
  assert.equal({}.polluted, undefined);
  assert.throws(() => entry(good, { kind: 'income', accountId: 'missing', amount: '1' }));
  assert.throws(() => entry(good, { kind: 'expense', accountId: good.accounts[0].id, amount: '-1' }));
  assert.throws(() => entry(good, { kind: 'transfer', accountId: good.accounts[0].id, toAccountId: good.accounts[0].id, amount: '1' }));
});

test('date validation handles leap years, invalid months, and pre-opening entries', () => {
  const state = account(emptyState(), 'bank', '0', '闰日账户', '2024-02-29');
  assert.equal(state.accounts[0].openingDate, '2024-02-29');
  for (const value of ['1900-02-29', '2026-04-31', '2026-13-01', '2026-00-01', '0000-01-01', '2026-1-01']) assert.throws(() => account(emptyState(), 'bank', '0', 'bad', value));
  assert.doesNotThrow(() => account(emptyState(), 'bank', '0', 'century', '2000-02-29'));
  assert.throws(() => entry(state, { kind: 'income', accountId: state.accounts[0].id, amount: '1', date: '2024-02-28' }), /早于/);
  assert.throws(() => overview(state, '2026-13'));
  assert.throws(() => balances(state, '2026-02-30'));
});

test('editing or voiding an earlier entry cannot silently create impossible later repayments', () => {
  let state = account(emptyState(), 'bank', '1000');
  state = account(state, 'loan', '100');
  const [bank, debt] = state.accounts.map(value => value.id);
  state = entry(state, { kind: 'adjustment', accountId: debt, amount: '500', date: '2026-10-01' });
  const adjustment = state.entries[0].id;
  state = entry(state, { kind: 'repayment', accountId: bank, toAccountId: debt, amount: '300', principal: '300', interest: '0', date: '2026-10-02' });
  assert.throws(() => voidEntry(state, adjustment), /超过/);
  assert.throws(() => saveEntry(state, { id: adjustment, amount: '200' }), /超过/);
  assert.equal(balances(state)[debt], '200');
});

test('default balances and monthly overview stop at local today, not future records', () => {
  const now = new Date();
  const local = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const month = local.slice(0, 7);
  let state = account(emptyState(), 'bank', '10000', '当前账户', local);
  const id = state.accounts[0].id;
  state = saveEntry(state, { kind: 'income', date: local, accountId: id, amount: '100' });
  state = saveEntry(state, { kind: 'income', date: '9999-12-31', accountId: id, amount: '5000' });
  state = account(state, 'bank', null, '未来未知账户', '9999-12-31');
  assert.equal(balances(state)[id], '10100');
  assert.equal(balances(state, '9999-12-31')[id], '15100');
  assert.equal(overview(state, month).assets, '10100');
  assert.equal(overview(state, month).income, '100');
  assert.ok(overview(state, month).history.every(value => value.date <= local));
});

test('restored audit/import containers reject duplicates and malformed audit while accepting P1 import audit', () => {
  let state = account(emptyState());
  const bad = action => { const copy = deep(state); action(copy); assert.throws(() => validateState(copy)); };
  bad(value => value.audit.push(deep(value.audit[0])));
  bad(value => value.audit = [null]);
  bad(value => value.audit = [12]);
  bad(value => value.audit[0].at = '2026-02-30T00:00:00.000Z');
  bad(value => value.audit[0].after.openingBalance = 0.1);
  bad(value => value.audit[0].after.id = 'wrong');
  bad(value => value.imports = [null]);
  bad(value => value.imports = ['not-an-import']);
  bad(value => value.imports = [{ id: 'duplicate' }, { id: 'duplicate' }]);
  bad(value => value.imports = [{ id: 'a', fingerprint: 'a'.repeat(64) }, { id: 'b', fingerprint: 'a'.repeat(64) }]);
  state.imports.push({ id: 'p1-history', fingerprint: 'b'.repeat(64) });
  state.audit.push({ id: 'import-audit', action: 'import-p1', entityId: 'p1-history', at: '2026-10-26T00:00:00.000Z' });
  assert.equal(validateState(state), state);
});

test('precision bounds reject overlarge amounts rather than returning unusable cents', () => {
  assert.throws(() => parseMoney('9'.repeat(60)), /范围/);
  assert.equal(formatMoney(parseMoney('9'.repeat(58))), `¥${'9'.repeat(58).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.00`);
});

import {emptyPlanning, validatePlanning} from './planning.js';
// All money stays in integer cents. No market value is inferred from imported positions.
const ASSETS = new Set(['bank', 'cash', 'securities', 'fund', 'other']);
const DEBTS = new Set(['loan', 'credit']);
const INVESTMENTS = new Set(['securities', 'fund']);
const CASH = new Set(['bank', 'cash']);
const KINDS = new Set(['income', 'expense', 'transfer', 'adjustment', 'repayment', 'buy', 'sell', 'dividend']);
const RESERVED = new Set(['__proto__', 'prototype', 'constructor']);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const fail = message => { throw new Error(message); };
const clone = value => structuredClone(value);
const uuid = () => globalThis.crypto.randomUUID();
const today = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail(`${label}必须是普通对象`);
}
function text(value, label, max = 10000) {
  if (typeof value !== 'string' || value.length > max) fail(`${label}格式无效`);
}
function identifier(value, label) {
  text(value, label, 128);
  if (!value.trim() || RESERVED.has(value) || /[\u0000-\u001f]/.test(value)) fail(`${label}无效`);
}
function date(value, label = '日期') {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) fail(`${label}必须为 YYYY-MM-DD`);
  const [year, month, day] = value.split('-').map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]) fail(`${label}不是有效日历日期`);
  return value;
}
function timestamp(value, label) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString() !== value) fail(`${label}无效`);
}
function cents(value, label, { nullable = false, nonnegative = false, positive = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || !/^-?(?:0|[1-9]\d{0,59})$/.test(value) || value === '-0') fail(`${label}必须为规范的整数分字符串`);
  const number = BigInt(value);
  if (nonnegative && number < 0n || positive && number <= 0n) fail(`${label}必须${positive ? '大于' : '不小于'}零`);
  return number;
}
function jsonSafe(value, depth = 0) {
  if (depth > 32) fail('数据嵌套过深');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if (Array.isArray(value)) { for (const item of value) jsonSafe(item, depth + 1); return; }
  object(value, '数据');
  for (const [key, item] of Object.entries(value)) {
    if (RESERVED.has(key)) fail('数据包含不安全字段');
    jsonSafe(item, depth + 1);
  }
}

export function emptyState() {
  return { schemaVersion: 1, accounts: [], entries: [], imports: [], audit: [], planning: emptyPlanning() };
}

export function parseMoney(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'string') fail('金额请输入十进制文本');
  const clean = value.trim();
  if (!clean) return null;
  if (!/^[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(clean) || clean.length > 60) fail('金额最多两位小数，不支持科学计数法');
  const negative = clean.startsWith('-');
  const [whole, decimal = ''] = clean.replace(/^[+-]/, '').split('.');
  const result = BigInt(whole || '0') * 100n + BigInt(decimal.padEnd(2, '0') || '0');
  if (result.toString().length > 60) fail('金额超出支持范围');
  return (negative ? -result : result).toString();
}

export function formatMoney(value) {
  if (value === null || value === undefined) return '未知';
  const money = cents(value, '金额');
  const absolute = money < 0n ? -money : money;
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${money < 0n ? '-' : ''}¥${whole}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

function validateEntry(entry, accounts) {
  object(entry, '流水');
  identifier(entry.id, '流水编号');
  if (!KINDS.has(entry.kind)) fail('未知流水类型');
  date(entry.date, '流水日期');
  identifier(entry.accountId, '流水账户');
  const account = accounts.get(entry.accountId);
  if (!account) fail('流水引用了不存在的账户');
  if (entry.date < account.openingDate) fail('流水不能早于账户起始日期');
  const isAdjustment = entry.kind === 'adjustment';
  cents(entry.amount, '流水金额', { positive: !isAdjustment, nonnegative: isAdjustment && DEBTS.has(account.type) });
  if (typeof entry.voided !== 'boolean') fail('流水撤销标记无效');
  text(entry.note, '流水备注');
  text(entry.category, '流水分类', 100);
  timestamp(entry.createdAt, '流水创建时间');
  const paired = ['transfer', 'buy', 'sell', 'repayment'].includes(entry.kind);
  let target;
  if (paired) {
    identifier(entry.toAccountId, '转入账户');
    target = accounts.get(entry.toAccountId);
    if (!target || target.id === account.id) fail('必须选择不同且存在的转入账户');
    if (entry.date < target.openingDate) fail('流水不能早于转入账户起始日期');
  } else if (entry.toAccountId !== undefined && entry.toAccountId !== null && entry.toAccountId !== '') fail('该流水类型不允许转入账户');
  if (!isAdjustment && !ASSETS.has(account.type)) fail('此流水的转出／收支账户必须是资产账户');
  if (entry.kind === 'transfer' && !ASSETS.has(target.type)) fail('转账只能在资产账户之间进行');
  if (entry.kind === 'buy' && (!CASH.has(account.type) || !INVESTMENTS.has(target.type))) fail('买入需要现金／银行账户转入证券／基金账户');
  if (entry.kind === 'sell' && (!INVESTMENTS.has(account.type) || !CASH.has(target.type))) fail('卖出需要证券／基金账户转入现金／银行账户');
  if (entry.kind === 'repayment') {
    if (!DEBTS.has(target.type)) fail('还款目标必须是借款或信用卡负债');
    const principal = cents(entry.principal, '还款本金', { nonnegative: true });
    const interest = cents(entry.interest, '还款利息', { nonnegative: true });
    if (principal + interest !== BigInt(entry.amount)) fail('还款总额必须等于本金加利息');
  } else if (entry.principal !== undefined || entry.interest !== undefined) fail('只有还款可以包含本金和利息');
}

function replay(state, asOf) {
  const result = new Map(state.accounts.map(account => [account.id, account.openingDate <= asOf && account.openingBalance !== null ? BigInt(account.openingBalance) : null]));
  const add = (id, delta) => { if (result.get(id) !== null) result.set(id, result.get(id) + delta); };
  const ordered = state.entries.filter(entry => !entry.voided && entry.date <= asOf).slice().sort((a, b) => a.date.localeCompare(b.date) || a.createdAt.localeCompare(b.createdAt));
  for (const entry of ordered) {
    const amount = BigInt(entry.amount);
    switch (entry.kind) {
      case 'income': case 'dividend': add(entry.accountId, amount); break;
      case 'expense': add(entry.accountId, -amount); break;
      case 'transfer': case 'buy': case 'sell': add(entry.accountId, -amount); add(entry.toAccountId, amount); break;
      case 'adjustment': result.set(entry.accountId, amount); break;
      case 'repayment':
        if (result.get(entry.toAccountId) !== null && BigInt(entry.principal) > result.get(entry.toAccountId)) fail('还款本金不能超过当时已知负债余额');
        add(entry.accountId, -amount);
        add(entry.toAccountId, -BigInt(entry.principal));
        break;
    }
  }
  return result;
}

export function validateState(state) {
  object(state, '账本');
  jsonSafe(state);
  if (state.schemaVersion !== 1) fail('不支持的数据结构版本');
  if (own(state, 'planning')) validatePlanning(state.planning);
  for (const key of ['accounts', 'entries', 'imports', 'audit']) if (!Array.isArray(state[key])) fail(`${key}必须是数组`);
  if (state.accounts.length > 10000 || state.entries.length > 200000 || state.imports.length > 10000 || state.audit.length > 400000) fail('账本记录数量超出本机保护上限');
  const accounts = new Map();
  for (const account of state.accounts) {
    object(account, '账户');
    identifier(account.id, '账户编号');
    if (accounts.has(account.id)) fail('账户编号重复');
    accounts.set(account.id, account);
    text(account.name, '账户名称', 200);
    if (!account.name.trim()) fail('请输入账户名称');
    if (!ASSETS.has(account.type) && !DEBTS.has(account.type)) fail('未知账户类型');
    cents(account.openingBalance, '初始余额', { nullable: true, nonnegative: DEBTS.has(account.type) });
    date(account.openingDate, '账户起始日期');
    text(account.note, '账户备注');
    if (typeof account.archived !== 'boolean') fail('账户归档标记无效');
    if (account.annualRate !== null && (typeof account.annualRate !== 'string' || !/^\d{1,4}(?:\.\d{1,6})?$/.test(account.annualRate))) fail('年利率请输入非负十进制文本或保留未知');
  }
  const ids = new Set();
  for (const entry of state.entries) {
    validateEntry(entry, accounts);
    if (ids.has(entry.id)) fail('流水编号重复');
    ids.add(entry.id);
  }
  const importIds = new Set(), fingerprints = new Set();
  for (const item of state.imports) {
    object(item, '导入记录');
    identifier(item.id, '导入编号');
    if (importIds.has(item.id)) fail('导入编号重复');
    importIds.add(item.id);
    if (item.fingerprint !== undefined) {
      if (typeof item.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(item.fingerprint) || fingerprints.has(item.fingerprint)) fail('导入指纹无效或重复');
      fingerprints.add(item.fingerprint);
    }
  }
  const auditIds = new Set();
  for (const record of state.audit) {
    object(record, '审计记录');
    identifier(record.id, '审计编号');
    if (auditIds.has(record.id)) fail('审计编号重复');
    auditIds.add(record.id);
    identifier(record.entityId, '审计对象编号');
    timestamp(record.at, '审计时间');
    if (record.action === 'import-p1') {
      if (!importIds.has(record.entityId)) fail('导入审计引用了不存在的导入记录');
    } else {
      if (!['create', 'edit', 'archive', 'void'].includes(record.action) || !['account', 'entry'].includes(record.entityType)) fail('审计操作无效');
      if (record.action === 'create' ? record.before !== null : !record.before || typeof record.before !== 'object') fail('审计修改前记录无效');
      object(record.after, '审计修改后记录');
      if (record.after.id !== record.entityId || record.before && record.before.id !== record.entityId) fail('审计对象编号不一致');
      for (const snapshot of [record.before, record.after].filter(Boolean)) {
        if (record.entityType === 'account') cents(snapshot.openingBalance, '审计余额', { nullable: true, nonnegative: DEBTS.has(snapshot.type) });
        else {
          cents(snapshot.amount, '审计流水金额', { positive: snapshot.kind !== 'adjustment' });
          if (snapshot.kind === 'repayment') {
            const principal = cents(snapshot.principal, '审计还款本金', { nonnegative: true });
            const interest = cents(snapshot.interest, '审计还款利息', { nonnegative: true });
            if (principal + interest !== BigInt(snapshot.amount)) fail('审计还款总额无效');
          }
        }
      }
    }
  }
  replay(state, '9999-12-31');
  return state;
}

function audited(state, action, entityType, entityId, before, after) {
  state.audit.push({ id: uuid(), action, entityType, entityId, at: new Date().toISOString(), before: before === null ? null : clone(before), after: clone(after) });
  return validateState(state);
}

export function saveAccount(state, input) {
  validateState(state);
  object(input, '账户输入');
  const next = clone(state);
  const index = input.id ? next.accounts.findIndex(account => account.id === input.id) : -1;
  if (input.id && index < 0) fail('要修改的账户不存在');
  const before = index < 0 ? null : clone(next.accounts[index]);
  const value = { id: before?.id || uuid(), name: input.name ?? before?.name ?? '', type: input.type ?? before?.type ?? 'bank', openingBalance: own(input, 'openingBalance') ? input.openingBalance : before?.openingBalance ?? null, openingDate: input.openingDate ?? before?.openingDate ?? today(), note: input.note ?? before?.note ?? '', annualRate: own(input, 'annualRate') ? input.annualRate : before?.annualRate ?? null, archived: own(input, 'archived') ? input.archived : before?.archived ?? false };
  if (index < 0) next.accounts.push(value); else next.accounts[index] = value;
  return audited(next, before ? 'edit' : 'create', 'account', value.id, before, value);
}

export function archiveAccount(state, id) {
  validateState(state);
  const next = clone(state);
  const value = next.accounts.find(account => account.id === id);
  if (!value) fail('账户不存在');
  if (value.archived) return next;
  const before = clone(value);
  value.archived = true;
  return audited(next, 'archive', 'account', id, before, value);
}

export function saveEntry(state, input) {
  validateState(state);
  object(input, '流水输入');
  const next = clone(state);
  const index = input.id ? next.entries.findIndex(entry => entry.id === input.id) : -1;
  if (input.id && index < 0) fail('要修改的流水不存在');
  const before = index < 0 ? null : clone(next.entries[index]);
  const data = { ...before, ...input };
  const value = { id: before?.id || uuid(), kind: data.kind, date: data.date ?? today(), accountId: data.accountId, amount: data.amount, note: data.note ?? '', category: data.category ?? '', voided: before?.voided ?? false, createdAt: before?.createdAt || new Date().toISOString() };
  if (['transfer', 'buy', 'sell', 'repayment'].includes(value.kind)) value.toAccountId = data.toAccountId;
  if (value.kind === 'repayment') { value.principal = data.principal; value.interest = data.interest; }
  if (value.voided) fail('已撤销流水不能修改，请新增记录');
  for (const id of [value.accountId, value.toAccountId].filter(Boolean)) {
    const account = next.accounts.find(item => item.id === id);
    if (account?.archived && (!before || ![before.accountId, before.toAccountId].includes(id))) fail('已归档账户不能新增流水');
  }
  if (index < 0) next.entries.push(value); else next.entries[index] = value;
  return audited(next, before ? 'edit' : 'create', 'entry', value.id, before, value);
}

export function voidEntry(state, id) {
  validateState(state);
  const next = clone(state);
  const value = next.entries.find(entry => entry.id === id);
  if (!value) fail('流水不存在');
  if (value.voided) return next;
  const before = clone(value);
  value.voided = true;
  return audited(next, 'void', 'entry', id, before, value);
}

export function balances(state, asOf = today()) {
  validateState(state);
  date(asOf);
  return Object.fromEntries([...replay(state, asOf)].map(([id, value]) => [id, value === null ? null : value.toString()]));
}

function wealthAt(state, asOf) {
  const values = replay(state, asOf);
  let assets = 0n, liabilities = 0n, unknownAssets = 0, unknownLiabilities = 0;
  for (const account of state.accounts) {
    if (account.openingDate > asOf) continue;
    const value = values.get(account.id);
    if (ASSETS.has(account.type)) { if (value === null) unknownAssets++; else assets += value; }
    else { if (value === null) unknownLiabilities++; else liabilities += value; }
  }
  return { assets: unknownAssets ? null : assets.toString(), liabilities: unknownLiabilities ? null : liabilities.toString(), netWorth: unknownAssets || unknownLiabilities ? null : (assets - liabilities).toString(), unknownAssets, unknownLiabilities };
}

export function overview(state, month = today().slice(0, 7), asOf = today()) {
  validateState(state);
  if (typeof month !== 'string' || !/^\d{4}-(0[1-9]|1[0-2])$/.test(month) || month.startsWith('0000')) fail('月份必须为有效 YYYY-MM');
  date(asOf);
  let income = 0n, expense = 0n;
  for (const entry of state.entries) {
    if (entry.voided || entry.date > asOf || !entry.date.startsWith(`${month}-`)) continue;
    if (entry.kind === 'income' || entry.kind === 'dividend') income += BigInt(entry.amount);
    if (entry.kind === 'expense') expense += BigInt(entry.amount);
    if (entry.kind === 'repayment') expense += BigInt(entry.interest);
  }
  const dates = [...new Set([...state.accounts.map(account => account.openingDate), ...state.entries.filter(entry => !entry.voided).map(entry => entry.date)])].filter(value => value <= asOf).sort();
  return { ...wealthAt(state, asOf), income: income.toString(), expense: expense.toString(), surplus: (income - expense).toString(), history: dates.map(value => ({ date: value, netWorth: wealthAt(state, value).netWorth })) };
}

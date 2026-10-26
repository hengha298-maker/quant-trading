import { emptyState, parseMoney, formatMoney, saveAccount, archiveAccount, saveEntry, voidEntry, balances, overview } from './domain.js';
import { loadState, persistState, encryptBackup, decryptBackup, previewRestore, restoreMerge, restoreReplace, parseP1Import, applyP1Import, storageStatus, requestPersistence } from './io.js';

const $ = (id) => document.getElementById(id);
const typeNames = { bank: '银行账户', cash: '现金 / 支付账户', securities: '股票证券账户', fund: '基金账户', other: '其他资产', loan: '借款负债', credit: '信用卡负债' };
const kindNames = { income: '收入', expense: '支出', transfer: '账户转账', adjustment: '余额调整', repayment: '负债还款', buy: '投资买入', sell: '投资卖出', dividend: '投资分红' };
const kindIcons = { income: '↙', expense: '↗', transfer: '⇄', adjustment: '＝', repayment: '↗', buy: '↔', sell: '↔', dividend: '＋' };
const localDate = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const today = () => localDate();
const isDebt = (account) => ['loan', 'credit'].includes(account.type);
let state = emptyState();
let bootError = '';
let busy = false;
let activeTab = 'overview';
let selectedAccount = '';
let pendingRestore = null;
let pendingImport = null;
let backedUpCurrent = false;
let toastTimer;
let staleData = false;
const warningMessages = new Map();
const writeChannel = 'BroadcastChannel' in window ? new BroadcastChannel('goose-v3-write-events') : null;

function node(tag, className, content) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (content !== undefined && content !== null) result.textContent = String(content);
  return result;
}
function fill(target, children) { target.replaceChildren(...children); }
function money(value) { return value === null || value === undefined ? '未知' : formatMoney(value); }
function inputMoney(value) {
  if (value === null || value === undefined) return '';
  const cents = BigInt(value);
  const sign = cents < 0n ? '-' : '';
  const abs = cents < 0n ? -cents : cents;
  return `${sign}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
}
function info(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 4200);
}
function report(error, target) {
  const message = error instanceof Error ? error.message : String(error);
  if (target) $(target).textContent = message;
  else info(message);
}
function warn(message, key = 'data') {
  if (message) warningMessages.set(key, message); else warningMessages.delete(key);
  $('global-warning').textContent = [...warningMessages.values()].join(' ');
  $('global-warning').classList.toggle('hidden', !warningMessages.size);
  if (staleData) {
    const button = node('button', 'text-button', '重新载入最新记录');
    button.type = 'button'; button.addEventListener('click', () => location.reload());
    $('global-warning').append(document.createTextNode(' '), button);
  }
}
function accountName(id) { return state.accounts.find((account) => account.id === id)?.name || '未知账户'; }
function hasRecords() { return state.accounts.length > 0 || state.entries.length > 0 || (state.imports || []).length > 0; }

async function commit(next, success, { recovery = false } = {}) {
  if (busy) throw new Error('正在保存，请稍候再试。');
  if (staleData) throw new Error('另一窗口已更新账本。已暂停本窗口保存，请重新载入最新记录，未保存表单请先记下。');
  if (bootError && !recovery) throw new Error('本机数据读取异常，已暂停普通保存。请在“设置与备份”中验证备份并明确恢复。');
  busy = true;
  try {
    const saved = await persistState(next);
    state = saved && saved.accounts && saved.entries ? saved : next;
    bootError = '';
    backedUpCurrent = false;
    pendingRestore = null;
    $('restore-preview').classList.add('hidden');
    warn('');
    render();
    writeChannel?.postMessage({ type: 'saved' });
    if (success) info(success);
  } catch (error) {
    if (error.message.includes('另一窗口')) {
      staleData = true;
      warn('另一窗口已更新账本。此窗口已暂停保存，请先记下未保存表单，再重新载入最新记录。', 'concurrent');
    }
    throw error;
  } finally { busy = false; }
}

function switchTab(tab, updateHash = true) {
  if (!['overview', 'assets', 'entries', 'settings'].includes(tab)) tab = 'overview';
  activeTab = tab;
  document.querySelectorAll('.page').forEach((page) => page.classList.toggle('hidden', page.id !== `page-${tab}`));
  document.querySelectorAll('.bottom-nav [data-tab]').forEach((button) => {
    if (button.dataset.tab === tab) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (updateHash) history.replaceState(null, '', `#${tab}`);
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function entryNode(entry) {
  const button = node('button', `entry-item${entry.voided ? ' voided' : ''}`);
  button.type = 'button';
  button.dataset.entryId = entry.id;
  button.append(node('span', 'entry-icon', kindIcons[entry.kind] || '·'));
  const body = node('span', 'entry-content');
  body.append(node('span', 'entry-title', entry.note || entry.category || kindNames[entry.kind]));
  const direction = entry.toAccountId ? ` → ${accountName(entry.toAccountId)}` : '';
  body.append(node('span', 'entry-description', `${entry.date} · ${kindNames[entry.kind]} · ${accountName(entry.accountId)}${direction}${entry.voided ? ' · 已撤销' : ''}`));
  button.append(body);
  const sign = ['income', 'dividend'].includes(entry.kind) ? '+' : entry.kind === 'expense' ? '−' : '';
  const amount = node('span', `entry-amount ${['income', 'dividend'].includes(entry.kind) ? 'income' : entry.kind === 'expense' ? 'expense' : ''}`, sign + money(entry.amount));
  button.append(amount);
  button.addEventListener('click', () => openEntry(entry.id));
  return button;
}

function renderOverview() {
  $('today-label').textContent = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' }).format(new Date());
  const result = overview(state, $('overview-month').value || today().slice(0, 7), today());
  $('net-worth').textContent = money(result.netWorth);
  $('total-assets').textContent = money(result.assets);
  $('total-liabilities').textContent = money(result.liabilities);
  $('month-income').textContent = money(result.income);
  $('month-expense').textContent = money(result.expense);
  $('month-surplus').textContent = money(result.surplus);
  const unknown = Number(result.unknownAssets || 0) + Number(result.unknownLiabilities || 0);
  $('net-worth-note').textContent = unknown ? `${unknown} 个账户余额未知 · 不以零代替，完整总额暂不可知` : `截至 ${today()} · 依据本机记录，非实时市值`;
  $('empty-overview').classList.toggle('hidden', hasRecords());
  const dates = (result.history || []).filter((point) => point.date <= today());
  const displayed = dates.slice(-7);
  const known = displayed.filter((point) => point.netWorth !== null && point.netWorth !== undefined);
  let maximum = known.reduce((max, point) => { const value = BigInt(point.netWorth); const abs = value < 0n ? -value : value; return abs > max ? abs : max; }, 1n);
  fill($('history-chart'), displayed.length ? displayed.map((point) => {
    const column = node('div', 'history-column');
    const value = point.netWorth === null || point.netWorth === undefined ? null : BigInt(point.netWorth);
    column.append(node('span', 'history-value', value === null ? '未知' : money(point.netWorth)));
    const bar = node('span', `history-bar${value !== null && value < 0n ? ' negative' : ''}`);
    bar.style.height = value === null ? '3px' : `${Math.max(3, Number(((value < 0n ? -value : value) * 10000n) / maximum) / 100)}%`;
    column.append(bar, node('span', 'history-date', point.date.slice(5)));
    column.setAttribute('aria-label', `${point.date} 净资产 ${money(point.netWorth)}`);
    return column;
  }) : [node('p', 'empty-list', '记录账户与余额变化后，足迹会出现在这里。')]);
  fill($('history-table'), dates.length ? dates.slice().reverse().map((point) => {
    const row = node('div', 'history-row'); row.append(node('span', '', point.date), node('strong', '', money(point.netWorth))); return row;
  }) : [node('p', 'empty-list', '暂无历史记录')]);
  const recent = state.entries.filter((entry) => !entry.voided && entry.date <= today()).sort((a, b) => b.date.localeCompare(a.date) || (b.updatedAt || '').localeCompare(a.updatedAt || '')).slice(0, 4);
  fill($('recent-entries'), recent.length ? recent.map(entryNode) : [node('p', 'empty-list', '还没有流水。点“记一笔”开始记录。')]);
}

function renderAssets() {
  const currentBalances = balances(state, today());
  const active = state.accounts.filter((account) => !account.archived || $('show-archived').checked);
  $('account-count').textContent = `${active.length} 个账户`;
  fill($('account-list'), active.length ? active.map((account) => {
    const button = node('button', `account-item${account.archived ? ' archived' : ''}`);
    button.type = 'button'; button.dataset.accountId = account.id;
    const top = node('span', 'account-top');
    top.append(node('span', 'account-icon', isDebt(account) ? '−' : account.type === 'securities' || account.type === 'fund' ? '↗' : '▣'));
    const caption = node('span');
    caption.append(node('span', 'account-name', account.name), node('span', 'account-type', `${typeNames[account.type]}${account.archived ? ' · 已归档' : ''}`));
    top.append(caption);
    button.append(top, node('span', 'account-money', money(currentBalances[account.id])));
    button.addEventListener('click', () => showAccountDetail(account.id));
    return button;
  }) : [node('p', 'empty-list', '尚未添加账户。可以添加余额未知的账户。')]);
  renderPositions();
}

function positionNodes(positions) {
  if (!Array.isArray(positions) || !positions.length) return [node('p', 'helper', '此导入没有可显示的持仓。')];
  return positions.map((position) => {
    const row = node('div', 'position-row');
    const symbol = position.symbol || position.code || position.security_id || '';
    const historicalNames = { '600900.SH': '长江电力', '515450.SH': '红利低波 ETF（历史名称）' };
    const name = position.name || position.security_name || historicalNames[symbol] || symbol || '历史持仓';
    const qty = position.quantity ?? position.shares ?? position.units ?? '未知';
    row.append(node('span', '', `${name}${symbol && symbol !== name ? ` · ${symbol}` : ''}`), node('span', '', `${qty} ${position.unit || '股/份'}`));
    return row;
  });
}

function renderPositions() {
  const imports = state.imports || [];
  fill($('position-list'), imports.length ? imports.map((item) => {
    const section = node('div', 'position-item');
    const source = typeof item.source === 'object' ? (item.source.label || item.source.name || 'P1 历史快照') : item.source || 'P1 历史快照';
    const label = node('p', 'position-source');
    label.append(node('span', 'reference-badge', '历史参考'), document.createTextNode(`${source} · ${item.asOf || item.as_of || item.importedAt || '时间未知'}`));
    section.append(label, ...positionNodes(item.positions || (state.positions || []).filter((position) => position.importId === item.id || position.importFingerprint === item.fingerprint)));
    section.append(node('p', 'helper', '成本、现金余额、当前市值与盈亏：未知。未计入财富总额。'));
    return section;
  }) : [node('p', 'empty-list', '未导入历史快照。导入由你手动选择文件。')]);
}

function renderEntries() {
  const query = $('entry-search').value.trim().toLocaleLowerCase();
  const kind = $('entry-filter-kind').value;
  const from = $('entry-filter-from').value;
  const to = $('entry-filter-to').value;
  const entries = state.entries.filter((entry) => {
    if (entry.voided && !$('show-voided').checked) return false;
    if (kind && entry.kind !== kind) return false;
    if (from && entry.date < from || to && entry.date > to) return false;
    return !query || `${entry.note || ''} ${entry.category || ''} ${accountName(entry.accountId)} ${accountName(entry.toAccountId)} ${kindNames[entry.kind]} ${entry.date}`.toLocaleLowerCase().includes(query);
  }).sort((a, b) => b.date.localeCompare(a.date) || (b.updatedAt || '').localeCompare(a.updatedAt || ''));
  $('entries-count').textContent = `${entries.length} 笔记录 · 点选可修改或撤销`;
  const elements = [];
  let previousDate = '';
  for (const entry of entries) {
    if (entry.date !== previousDate) { elements.push(node('h2', 'day-label', entry.date)); previousDate = entry.date; }
    elements.push(entryNode(entry));
  }
  fill($('entry-list'), elements.length ? elements : [node('p', 'empty-list', '没有符合条件的记录。')]);
}

function render() {
  renderOverview(); renderAssets(); renderEntries();
  $('schema-version').textContent = state.schemaVersion ?? state.version ?? '1';
}

function openAccount(id = '') {
  if (bootError) { info('本机记录读取异常，请先到设置恢复备份。'); return; }
  const form = $('account-form'); form.reset();
  const account = state.accounts.find((item) => item.id === id);
  form.elements.id.value = account?.id || '';
  form.elements.name.value = account?.name || '';
  form.elements.type.value = account?.type || 'bank';
  form.elements.openingBalance.value = inputMoney(account?.openingBalance ?? null);
  form.elements.openingDate.value = account?.openingDate || today();
  form.elements.annualRate.value = account?.annualRate ?? '';
  form.elements.note.value = account?.note || '';
  $('account-dialog-title').textContent = account ? '编辑账户' : '添加账户';
  $('archive-account').classList.toggle('hidden', !account);
  $('archive-account').textContent = account?.archived ? '取消归档，恢复显示' : '归档账户，保留历史记录';
  $('account-error').textContent = '';
  updateAccountFields();
  $('account-dialog').showModal();
}
function updateAccountFields() {
  const debt = ['loan', 'credit'].includes($('account-form').elements.type.value);
  $('annual-rate-field').classList.toggle('hidden', !debt);
  $('account-balance-label').textContent = debt ? '初始欠款余额（元，可留空表示未知）' : '初始余额（元，可留空表示未知）';
}
function showAccountDetail(id) {
  const account = state.accounts.find((item) => item.id === id);
  if (!account) return;
  selectedAccount = id;
  $('account-detail-title').textContent = account.name;
  $('account-detail-balance').textContent = money(balances(state, today())[id]);
  $('account-detail-note').textContent = [typeNames[account.type], account.annualRate !== null && account.annualRate !== undefined ? `年利率 ${account.annualRate}%（不自动计息）` : '', account.note || '', account.archived ? '已归档；归档账户仍计入余额，除非已调整为零。' : ''].filter(Boolean).join(' · ');
  const dates = [...new Set([account.openingDate, ...state.entries.filter((entry) => !entry.voided && (entry.accountId === id || entry.toAccountId === id)).map((entry) => entry.date)])].filter((date) => date && date <= today()).sort().reverse();
  fill($('account-history'), dates.map((date) => {
    const row = node('div', 'history-row'); row.append(node('span', '', date), node('strong', '', money(balances(state, date)[id]))); return row;
  }));
  $('adjust-account').disabled = !!account.archived;
  $('account-detail-dialog').showModal();
}

function chooseAccounts(select, accounts, value) {
  fill(select, accounts.map((account) => {
    const option = node('option', '', `${account.name}${account.archived ? '（已归档）' : ''}`);
    option.value = account.id; return option;
  }));
  if (accounts.some((account) => account.id === value)) select.value = value;
  if (!accounts.length) { const option = node('option', '', '请先添加适用账户'); option.value = ''; select.append(option); }
}
function updateEntryFields() {
  const form = $('entry-form');
  const kind = form.elements.kind.value;
  const original = state.entries.find((entry) => entry.id === form.elements.id.value);
  const sourceValue = form.elements.accountId.value || original?.accountId;
  const targetValue = form.elements.toAccountId.value || original?.toAccountId;
  const available = state.accounts.filter((account) => !account.archived || original && [original.accountId, original.toAccountId].includes(account.id));
  let sources = available.filter((account) => !isDebt(account));
  let targets = sources;
  if (kind === 'adjustment') sources = available;
  if (kind === 'repayment') targets = available.filter(isDebt);
  if (kind === 'buy') { sources = available.filter((account) => ['bank', 'cash'].includes(account.type)); targets = available.filter((account) => ['securities', 'fund'].includes(account.type)); }
  if (kind === 'sell') { sources = available.filter((account) => ['securities', 'fund'].includes(account.type)); targets = available.filter((account) => ['bank', 'cash'].includes(account.type)); }
  chooseAccounts(form.elements.accountId, sources, sourceValue);
  chooseAccounts(form.elements.toAccountId, targets, targetValue);
  if (form.elements.toAccountId.value === form.elements.accountId.value && targets.length > 1) form.elements.toAccountId.value = targets.find((account) => account.id !== form.elements.accountId.value).id;
  const multiple = ['transfer', 'repayment', 'buy', 'sell'].includes(kind);
  $('entry-to-field').classList.toggle('hidden', !multiple);
  form.elements.toAccountId.required = multiple;
  $('entry-account-label').textContent = ['income', 'dividend', 'sell'].includes(kind) ? kind === 'sell' ? '卖出所在证券 / 基金账户' : '收款账户' : kind === 'adjustment' ? '调整账户' : '付款 / 转出账户';
  $('entry-to-label').textContent = kind === 'repayment' ? '偿还的负债账户' : kind === 'buy' ? '买入所在证券 / 基金账户' : '收款 / 转入账户';
  $('entry-amount-label').textContent = kind === 'adjustment' ? '调整后的余额（元）' : kind === 'repayment' ? '还款合计（本金＋利息）' : '金额（元）';
  $('repayment-fields').classList.toggle('hidden', kind !== 'repayment');
  form.elements.amount.readOnly = kind === 'repayment';
  form.elements.principal.required = kind === 'repayment';
  form.elements.interest.required = kind === 'repayment';
  const notes = { income: '收入增加所选账户余额。未知余额仍保持未知。', expense: '支出减少所选账户余额。金额与账户是必填项，其他可以稍后补充。', transfer: '转账只改变两个账户余额，不重复计算收入和支出。', adjustment: '填写当前明确余额，保留调整记录。可将原本未知的余额更新为已知。', repayment: '请分别填写本金和利息；无利息请填 0。本金减少欠款，只有利息计为支出。', buy: '成交额由现金 / 银行转入证券 / 基金账户。不推算实时价格、成本或盈亏，也不重复计入支出。', sell: '成交额由证券 / 基金转入现金 / 银行账户。不重复计入收入，不推算盈亏。', dividend: '分红增加收款账户，并计入本月收入。' };
  $('entry-kind-help').textContent = notes[kind];
  if (kind === 'repayment') updateRepaymentTotal();
}
function updateRepaymentTotal() {
  const form = $('entry-form');
  try {
    const principal = parseMoney(form.elements.principal.value);
    const interest = parseMoney(form.elements.interest.value);
    form.elements.amount.value = principal === null || interest === null ? '' : inputMoney(String(BigInt(principal) + BigInt(interest)));
  } catch { form.elements.amount.value = ''; }
}
function openEntry(id = '', preset = {}) {
  if (bootError) { info('本机记录读取异常，请先到设置恢复备份。'); return; }
  if (!state.accounts.some((account) => !account.archived)) { info('请先添加一个账户，余额可以留为未知。'); switchTab('assets'); openAccount(); return; }
  const form = $('entry-form'); form.reset();
  const entry = state.entries.find((item) => item.id === id);
  if (entry?.voided) { info('此记录已撤销，原始痕迹保留。需要重新记录时请添加新记录。'); return; }
  form.elements.id.value = entry?.id || '';
  form.elements.kind.value = entry?.kind || preset.kind || 'expense';
  form.elements.date.value = entry?.date || today();
  form.elements.amount.value = entry ? inputMoney(entry.amount) : '';
  form.elements.principal.value = entry ? inputMoney(entry.principal) : '';
  form.elements.interest.value = entry ? inputMoney(entry.interest) : '';
  form.elements.note.value = entry?.note || '';
  form.elements.category.value = entry?.category || '';
  fill(form.elements.accountId, []); fill(form.elements.toAccountId, []);
  updateEntryFields();
  if (entry || preset.accountId) form.elements.accountId.value = entry?.accountId || preset.accountId;
  if (entry?.toAccountId) form.elements.toAccountId.value = entry.toAccountId;
  $('entry-dialog-title').textContent = entry ? '修改记录' : preset.kind === 'adjustment' ? '记录余额调整' : '快速记一笔';
  $('void-entry').classList.toggle('hidden', !entry);
  $('entry-error').textContent = '';
  $('entry-dialog').showModal();
  if (!entry) setTimeout(() => form.elements.amount.focus(), 30);
}

async function refreshStorageStatus() {
  try {
    const status = await storageStatus();
    const usage = status.usage === undefined ? '未知' : `${(status.usage / (1024 * 1024)).toFixed(2)} MiB`;
    const quota = status.quota === undefined ? '未知' : `${(status.quota / (1024 * 1024)).toFixed(0)} MiB`;
    $('storage-info').textContent = `持久存储：${status.persisted ? '浏览器已允许（仍需备份）' : '未获长期保留承诺'}。已用 ${usage} / 浏览器额度 ${quota}。本机数据不发送至服务器。`;
    if (status.warning) warn(status.warning);
  } catch (error) { $('storage-info').textContent = `无法读取存储状态：${error.message}`; }
}
function downloadJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = node('a'); anchor.href = url; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
async function fileText(input, label, limitMiB = 12) {
  const file = input.files?.[0];
  if (!file) throw new Error(`请选择${label}文件。`);
  if (file.size > limitMiB * 1024 * 1024) throw new Error(`文件超过 ${limitMiB} MiB 限制，请确认选择了正确文件。`);
  return file.text();
}
function countValue(value) { return Array.isArray(value) ? value.length : Number(value || 0); }
function showRestorePreview() {
  const preview = previewRestore(state, pendingRestore);
  fill($('restore-summary'), [node('h3', '', '备份已通过验证'), node('p', 'helper', `待导入：${countValue(preview.accounts)} 个账户，${countValue(preview.entries)} 笔流水，${countValue(preview.imports)} 份历史快照。`)]);
  const conflicts = countValue(preview.conflicts);
  $('restore-conflicts').textContent = conflicts ? `发现 ${conflicts} 项相同 ID 内容冲突，禁止合并。完整替换需要先导出当前数据并明确确认。` : '未发现同 ID 内容冲突。合并会保留当前记录并去重。';
  $('merge-restore').disabled = conflicts > 0 || !!bootError;
  $('replace-confirm').checked = false;
  if (bootError) $('restore-conflicts').textContent += ' 当前本机库无法读取，只能选择明确完整恢复；原损坏数据不会被自动当作空账本。';
  $('restore-preview').classList.remove('hidden');
}

async function addDemo() {
  if (hasRecords()) throw new Error('虚构数据仅可添加到空账本，不会混入你的现有记录。');
  if (!confirm('将添加明确标注为虚构的账户与流水，用于体验。确认添加？')) return;
  const date = today();
  let next = emptyState();
  next = saveAccount(next, { name: '虚构 · 日常银行卡', type: 'bank', openingBalance: parseMoney('16800.00'), openingDate: date, note: '完全虚构的体验数据', annualRate: null });
  next = saveAccount(next, { name: '虚构 · 信用卡', type: 'credit', openingBalance: parseMoney('1200.00'), openingDate: date, note: '完全虚构的体验数据', annualRate: null });
  next = saveAccount(next, { name: '虚构 · 基金账户', type: 'fund', openingBalance: parseMoney('3200.00'), openingDate: date, note: '完全虚构的手工余额，不是实时估值。正式使用时未知金额请留空。', annualRate: null });
  const bank = next.accounts[0].id;
  next = saveEntry(next, { kind: 'income', date, accountId: bank, amount: parseMoney('8500.00'), note: '虚构 · 工资收入', category: '工资' });
  next = saveEntry(next, { kind: 'expense', date, accountId: bank, amount: parseMoney('28.50'), note: '虚构 · 午餐', category: '餐饮' });
  await commit(next, '虚构数据已添加，可实际修改、撤销、备份和恢复。');
  switchTab('overview');
}

function bindEvents() {
  document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => switchTab(button.dataset.tab)));
  document.querySelectorAll('[data-action="new-account"]').forEach((button) => button.addEventListener('click', () => openAccount()));
  document.querySelectorAll('[data-action="new-entry"]').forEach((button) => button.addEventListener('click', () => openEntry()));
  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(button.dataset.close).close()));
  document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
  $('quick-add').addEventListener('click', () => openEntry());
  $('refresh-overview').addEventListener('click', () => { renderOverview(); info('已依据本机记录刷新；未请求实时行情。'); });
  $('overview-month').addEventListener('change', renderOverview);
  $('show-archived').addEventListener('change', renderAssets);
  for (const id of ['entry-search', 'entry-filter-kind', 'entry-filter-from', 'entry-filter-to', 'show-voided']) $(id).addEventListener(id === 'entry-search' ? 'input' : 'change', renderEntries);
  $('account-form').elements.type.addEventListener('change', updateAccountFields);
  $('account-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('account-error').textContent = '';
    const form = event.currentTarget;
    try {
      const original = state.accounts.find((account) => account.id === form.elements.id.value);
      const next = saveAccount(state, { ...original, id: form.elements.id.value || undefined, name: form.elements.name.value.trim(), type: form.elements.type.value, openingBalance: parseMoney(form.elements.openingBalance.value), openingDate: form.elements.openingDate.value, annualRate: ['loan', 'credit'].includes(form.elements.type.value) && form.elements.annualRate.value.trim() !== '' ? form.elements.annualRate.value.trim() : null, note: form.elements.note.value.trim() });
      await commit(next, '账户已保存到本机。'); $('account-dialog').close();
    } catch (error) { report(error, 'account-error'); }
  });
  $('archive-account').addEventListener('click', async () => {
    const account = state.accounts.find((item) => item.id === $('account-form').elements.id.value);
    if (!account) return;
    if (!account.archived && !confirm('归档后将隐藏此账户，历史流水与余额继续保留。确认归档？')) return;
    try { const next = account.archived ? saveAccount(state, { ...account, archived: false }) : archiveAccount(state, account.id); await commit(next, account.archived ? '账户已恢复显示。' : '账户已归档，历史保留。'); $('account-dialog').close(); } catch (error) { report(error, 'account-error'); }
  });
  $('edit-account').addEventListener('click', () => { $('account-detail-dialog').close(); openAccount(selectedAccount); });
  $('adjust-account').addEventListener('click', () => { $('account-detail-dialog').close(); openEntry('', { kind: 'adjustment', accountId: selectedAccount }); });
  $('entry-kind').addEventListener('change', updateEntryFields);
  $('entry-form').elements.principal.addEventListener('input', updateRepaymentTotal);
  $('entry-form').elements.interest.addEventListener('input', updateRepaymentTotal);
  $('entry-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('entry-error').textContent = '';
    const form = event.currentTarget; const kind = form.elements.kind.value;
    try {
      const original = state.entries.find((entry) => entry.id === form.elements.id.value);
      const data = { ...original, id: form.elements.id.value || undefined, kind, accountId: form.elements.accountId.value, date: form.elements.date.value, amount: parseMoney(form.elements.amount.value), note: form.elements.note.value.trim(), category: form.elements.category.value.trim() };
      if (['transfer', 'repayment', 'buy', 'sell'].includes(kind)) data.toAccountId = form.elements.toAccountId.value;
      else delete data.toAccountId;
      if (kind === 'repayment') { data.principal = parseMoney(form.elements.principal.value); data.interest = parseMoney(form.elements.interest.value); }
      else { delete data.principal; delete data.interest; }
      if (data.amount === null) throw new Error('这笔记录的金额必须明确，请输入金额。');
      await commit(saveEntry(state, data), original ? '修改已保存，原始修订痕迹保留。' : '已保存到本机。'); $('entry-dialog').close();
    } catch (error) { report(error, 'entry-error'); }
  });
  $('void-entry').addEventListener('click', async () => {
    if (!confirm('撤销后此记录不再影响余额和收支，原始痕迹会保留。确认撤销？')) return;
    try { await commit(voidEntry(state, $('entry-form').elements.id.value), '记录已撤销，历史痕迹保留。'); $('entry-dialog').close(); } catch (error) { report(error, 'entry-error'); }
  });
  $('export-backup').addEventListener('click', async () => {
    const button = $('export-backup');
    try {
      if (bootError) throw new Error('当前数据读取异常，无法导出完整账本。请使用之前已保存的备份进行明确恢复。');
      const password = $('backup-password').value;
      if (password.length < 12) throw new Error('备份密码至少需要 12 个字符。');
      if (password !== $('backup-password-repeat').value) throw new Error('两次密码不一致。');
      button.disabled = true;
      const captured = JSON.stringify(state);
      const envelope = await encryptBackup(state, password);
      downloadJSON(envelope, `Goose-V3-${today()}-${Date.now()}.goose.json`);
      backedUpCurrent = captured === JSON.stringify(state);
      $('backup-status').textContent = `已于 ${new Date().toLocaleTimeString('zh-CN')} 生成加密备份下载。请确认文件已保存到“文件”App或独立备份位置；下载动作不保证文件已妥善保存。`;
      $('backup-password').value = ''; $('backup-password-repeat').value = '';
      info('加密备份文件已生成。请确认保存并保管密码。');
    } catch (error) { report(error); } finally { button.disabled = false; }
  });
  $('preview-restore').addEventListener('click', async () => {
    const button = $('preview-restore');
    try {
      pendingRestore = null; $('restore-preview').classList.add('hidden'); button.disabled = true;
      const text = await fileText($('restore-file'), '加密备份');
      pendingRestore = await decryptBackup(text, $('restore-password').value);
      $('restore-password').value = '';
      showRestorePreview(); info('备份验证通过。尚未写入本机。');
    } catch (error) { report(error); } finally { button.disabled = false; }
  });
  $('restore-file').addEventListener('change', () => { pendingRestore = null; $('restore-preview').classList.add('hidden'); });
  $('merge-restore').addEventListener('click', async () => {
    try {
      if (!pendingRestore) throw new Error('请先验证备份。');
      if (!confirm('将备份中的无冲突记录合并到当前本机账本。确认继续？')) return;
      await commit(restoreMerge(state, pendingRestore), '备份已合并到本机。');
    } catch (error) { report(error); }
  });
  $('replace-restore').addEventListener('click', async () => {
    try {
      if (!pendingRestore) throw new Error('请先验证备份。');
      if (!bootError && hasRecords() && !backedUpCurrent) throw new Error('替换前请在上方导出当前账本的加密备份，并确认文件保存。');
      if (!$('replace-confirm').checked) throw new Error('请先勾选完整替换的确认说明。');
      if (!confirm(bootError ? '本机库无法读取。你选择用已验证备份明确恢复账本，确认继续？' : '确认备份文件已保存，且使用刚验证的备份完整替换当前账本？')) return;
      await commit(restoreReplace(state, pendingRestore), '已从备份完整恢复到本机。', { recovery: true });
    } catch (error) { report(error); }
  });
  $('request-persistence').addEventListener('click', async () => { try { const granted = await requestPersistence(); await refreshStorageStatus(); info(granted ? '浏览器已允许持久存储，仍请定期备份。' : '浏览器暂未允许；请继续定期导出备份。'); } catch (error) { report(error); } });
  for (const id of ['load-demo', 'demo-overview']) $(id).addEventListener('click', () => addDemo().catch(report));
  $('import-p1-button').addEventListener('click', () => { pendingImport = null; $('p1-file').value = ''; $('p1-preview').classList.add('hidden'); $('confirm-p1').classList.add('hidden'); $('p1-error').textContent = ''; $('p1-dialog').showModal(); });
  $('p1-file').addEventListener('change', () => { pendingImport = null; $('p1-preview').classList.add('hidden'); $('confirm-p1').classList.add('hidden'); });
  $('preview-p1').addEventListener('click', async () => {
    try {
      $('p1-error').textContent = ''; pendingImport = null; $('confirm-p1').classList.add('hidden');
      const imported = await parseP1Import(await fileText($('p1-file'), 'P1 历史快照', 8));
      const duplicate = (state.imports || []).some((item) => item.fingerprint === imported.fingerprint);
      const source = typeof imported.source === 'object' ? imported.source.label || imported.source.name || 'P1 历史快照' : imported.source;
      fill($('p1-preview'), [node('h3', '', duplicate ? '此快照已导入' : '历史快照检查通过'), node('p', 'helper', `来源：${source || 'P1 历史快照'} · 时间：${imported.asOf || '未知'}`), node('p', 'helper', `SHA256 指纹：${imported.fingerprint}`), ...positionNodes(imported.positions), node('p', 'helper', '未知成本和现金余额保留未知。515450 等未核验历史行情不会用于估值、盈亏或交易建议。')]);
      $('p1-preview').classList.remove('hidden');
      if (!duplicate) { pendingImport = imported; $('confirm-p1').classList.remove('hidden'); }
    } catch (error) { report(error, 'p1-error'); }
  });
  $('confirm-p1').addEventListener('click', async () => {
    try { if (!pendingImport) throw new Error('请先读取并检查快照。'); await commit(applyP1Import(state, pendingImport), '历史快照已导入为独立参考，原 P1 文件未改变。'); pendingImport = null; $('p1-dialog').close(); } catch (error) { report(error, 'p1-error'); }
  });
  window.addEventListener('hashchange', () => switchTab(location.hash.slice(1), false));
  window.addEventListener('online', updateNetwork); window.addEventListener('offline', updateNetwork);
  window.addEventListener('pageshow', () => { updateNetwork(); refreshStorageStatus(); });
  if (writeChannel) writeChannel.onmessage = (event) => {
    if (event.data?.type !== 'saved') return;
    staleData = true;
    warn('另一窗口已更新账本。此窗口已暂停保存，请先记下未保存表单，再重新载入最新记录。', 'concurrent');
  };
}

function updateNetwork() {
  $('network-status').textContent = navigator.onLine ? '本机账本 · 当前在线' : '离线使用中 · 记录仍存本机';
}
async function registerOffline() {
  updateNetwork();
  if (!('serviceWorker' in navigator)) { $('offline-status').textContent = '此浏览器不支持离线安装'; return; }
  if (!window.isSecureContext) { $('offline-status').textContent = '离线安装需 HTTPS / localhost'; return; }
  try {
    const registration = await navigator.serviceWorker.register('./sw.js', { scope: './' });
    const ready = async () => {
      const controller = navigator.serviceWorker.controller;
      if (!controller) { $('offline-status').textContent = '正在准备离线资源，请稍候'; return; }
      $('offline-status').textContent = '正在核验离线资源…';
      try {
        const verified = await new Promise((resolve, reject) => {
          const channel = new MessageChannel();
          const timer = setTimeout(() => { channel.port1.close(); reject(new Error('离线资源核验超时')); }, 5000);
          channel.port1.onmessage = (event) => { clearTimeout(timer); channel.port1.close(); resolve(event.data?.ready === true); };
          controller.postMessage({ type: 'VERIFY_OFFLINE' }, [channel.port2]);
        });
        if (!verified) throw new Error('本机缓存不完整');
        $('offline-status').textContent = '离线就绪';
        warn('', 'offline');
      } catch (error) { $('offline-status').textContent = '离线未就绪，请保持在线'; warn(`${error.message}。请保持在线后重新打开，当前不能保证断网启动。`, 'offline'); }
    };
    navigator.serviceWorker.addEventListener('controllerchange', ready);
    await navigator.serviceWorker.ready;
    ready();
    const showUpdate = () => {
      if (!registration.waiting) return;
      $('apply-update').classList.remove('hidden');
      $('apply-update').onclick = () => { info('请先保存表单并导出备份，再关闭此应用的全部窗口，然后重新打开，以启用已缓存新版本。'); };
    };
    showUpdate();
    registration.addEventListener('updatefound', () => registration.installing?.addEventListener('statechange', showUpdate));
  } catch (error) { $('offline-status').textContent = '离线未就绪，请保持在线'; warn(`离线资源准备失败：${error.message}。当前不能保证断网后重新打开。`, 'offline'); }
}

async function init() {
  $('overview-month').value = today().slice(0, 7);
  bindEvents();
  try { state = await loadState(); }
  catch (error) { bootError = error.message; warn(`本机记录读取失败：${error.message}。已禁止普通保存，请到设置验证备份并明确恢复。`); switchTab('settings'); }
  try { render(); } catch (error) { bootError = error.message; warn(`本机账本无法显示：${error.message}。请到设置恢复备份，当前记录不会被自动覆盖。`); switchTab('settings'); }
  if (!bootError) switchTab(location.hash.slice(1) || 'overview', false);
  await refreshStorageStatus();
  registerOffline();
}
init();

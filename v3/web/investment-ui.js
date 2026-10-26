import { parseMoney, formatMoney } from './domain.js';
import { parseJsonStrict } from './io.js';
import { emptyPlanning, latestSnapshot, snapshotTotals, saveSnapshot, voidSnapshot, addWatch, removeWatch, saveReport, reportAssessment, calculateAllocation, saveSalaryPlan, confirmSalaryReceipt, buildReviewPackage, buildDailyDecision, researchComparison } from './planning.js';

const TYPES = { bank: '银行存款', cash: '现金 / 支付账户', securities: '证券账户总额', fund: '基金账户', other: '其他资产', loan: '借款负债', credit: '信用卡负债' };
const STANCES = { hold: '持有', watch: '观察', accumulate: '分批买入研究', reduce: '分批减仓研究' };
const QUALITY = { low: '低', medium: '中', high: '高' };
const $ = id => document.getElementById(id);
const n = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined && text !== null) el.textContent = String(text); return el; };
const money = value => value === null || value === undefined ? '未知' : formatMoney(value);
const debt = type => ['loan', 'credit'].includes(type);
const decimal = value => {
  if (value === null || value === undefined) return '';
  const cents = BigInt(value), abs = cents < 0n ? -cents : cents;
  return `${cents < 0n ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
};
function button(text, cls, fn, id) { const el = n('button', cls, text); el.type = 'button'; if (id) el.id = id; if (fn) el.addEventListener('click', fn); return el; }
function field(label, name, { value = '', type = 'text', required = false, placeholder, options, rows } = {}) {
  const wrapper = n('label', 'field', label);
  let input;
  if (options) { input = n('select'); for (const [value, caption] of options) { const option = n('option', '', caption); option.value = value; input.append(option); } }
  else if (rows) { input = n('textarea'); input.rows = rows; input.maxLength = 2000; }
  else { input = n('input'); input.type = type; if (type === 'text') input.maxLength = 200; }
  input.name = name; input.id = name; input.value = value; input.required = required;
  if (placeholder) input.placeholder = placeholder;
  wrapper.append(input); return { wrapper, input };
}
function amountField(label, name, value = null) { const result = field(label, name, { value: decimal(value), placeholder: '未知请留空；零请填 0' }); result.input.inputMode = 'decimal'; return result; }
function row(label, value, cls = '') { const result = n('div', `history-row ${cls}`); result.append(n('span', '', label), n('strong', '', value)); return result; }
function panel(title, action) { const element = n('article', 'panel investment-panel'); const header = n('div', 'section-heading'); header.append(n('h2', '', title)); if (action) header.append(action); element.append(header); return element; }
function dialog(id, title) { const el = n('dialog', 'investment-dialog'); el.id = id; const header = n('div', 'dialog-heading'); const heading = n('h2', '', title); heading.id = `${id}-title`; header.append(heading, button('×', 'icon-button', () => el.close())); el.setAttribute('aria-labelledby', heading.id); el.append(header); document.body.append(el); return el; }
function download(value, name) { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })); const link = n('a'); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000); }

export function mountInvestment({ getState, commit, info, today }) {
  const planning = () => getState().planning || emptyPlanning();
  const arrays = key => planning()[key] || [];
  const snapshots = () => arrays('snapshots');
  const plans = () => arrays('salaryPlans');
  const activeSnapshots = () => snapshots().filter(item => !item.voided && !snapshots().some(next => !next.voided && next.replacesId === item.id)).sort((a, b) => b.date.localeCompare(a.date) || (b.createdAt || '').localeCompare(a.createdAt || ''));
  const fail = (error, target) => { if (target) target.textContent = error.message || String(error); else info(error.message || String(error)); };
  let snapshotEditor = null;
  let salaryEditor = null;
  let receiptEditor = null;
  let reportEditor = null;
  let selectedReportId = '';

  function openSnapshot(snapshot = null) {
    if (!snapshotEditor) snapshotEditor = makeSnapshotDialog();
    snapshotEditor.open(snapshot);
  }
  function makeSnapshotDialog() {
    const box = dialog('snapshot-dialog', '记录资产快照');
    const form = n('form'); form.id = 'snapshot-form';
    const dateField = field('快照日期', 'snapshot-date', { type: 'date', required: true });
    const noteField = field('快照备注（可选）', 'snapshot-note', { rows: 2 });
    const list = n('div', 'investment-snapshot-rows'); list.id = 'snapshot-items';
    const error = n('p', 'form-error'); error.id = 'snapshot-error'; error.setAttribute('role', 'alert');
    const summary = n('div', 'investment-snapshot-preview'); summary.id = 'snapshot-totals-preview';
    let replacing = null;
    let itemRows = [];
    function preview() {
      try {
        const items = readItems();
        const totals = snapshotTotals({ id: 'ui-preview', date: dateField.input.value || today(), items, note: '', createdAt: new Date().toISOString(), voided: false, replacesId: null });
        summary.replaceChildren(row('总资产', money(totals.assets)), row('总负债', money(totals.liabilities)), row('净资产', money(totals.netWorth)));
      } catch { summary.textContent = '完整填写后显示快照合计；留空余额保持未知。'; }
    }
    function addRow(item = {}) {
      const holder = n('section', 'investment-snapshot-item');
      const heading = n('div', 'section-heading'); heading.append(n('h3', '', `账户 ${itemRows.length + 1}`));
      const itemId = item.id || crypto.randomUUID();
      const name = field('账户名称', `snapshot-name-${crypto.randomUUID()}`, { value: item.name || '', required: true, placeholder: '例如：工资卡、证券账户、房贷' });
      name.input.dataset.snapshotField = 'name';
      const type = field('资产 / 负债类型', `snapshot-type-${crypto.randomUUID()}`, { value: item.type || 'bank', options: Object.entries(TYPES) }); type.input.dataset.snapshotField = 'type';
      const balance = amountField('余额 / 欠款（元）', `snapshot-balance-${crypto.randomUUID()}`, item.balance ?? null); balance.input.dataset.snapshotField = 'balance';
      const rate = field('年利率（%，未知留空）', `snapshot-rate-${crypto.randomUUID()}`, { value: item.annualRate ?? '', placeholder: '例如：3.5' }); rate.input.inputMode = 'decimal'; rate.input.dataset.snapshotField = 'annualRate';
      const due = field('还款到期日（未知留空）', `snapshot-due-${crypto.randomUUID()}`, { type: 'date', value: item.dueDate || '' }); due.input.dataset.snapshotField = 'dueDate';
      const note = field('账户备注（可选）', `snapshot-item-note-${crypto.randomUUID()}`, { value: item.note || '', rows: 2 }); note.input.dataset.snapshotField = 'note';
      const debtFields = n('div', 'two-fields'); debtFields.append(rate.wrapper, due.wrapper);
      const details = n('details', 'extra-details'); details.append(n('summary', '', '利率、到期日与备注'), debtFields, note.wrapper);
      const model = { itemId, holder, name: name.input, type: type.input, balance: balance.input, rate: rate.input, due: due.input, note: note.input };
      heading.append(button('移除此行', 'text-button danger-text', () => { holder.remove(); itemRows = itemRows.filter(value => value !== model); preview(); }));
      holder.append(heading, name.wrapper, type.wrapper, balance.wrapper, details);
      holder.addEventListener('input', preview);
      list.append(holder); itemRows.push(model); preview();
    }
    function readItems() {
      return itemRows.map(item => ({ ...(item.itemId ? { id: item.itemId } : {}), name: item.name.value.trim(), type: item.type.value, balance: parseMoney(item.balance.value), annualRate: debt(item.type.value) && item.rate.value.trim() ? item.rate.value.trim() : null, dueDate: debt(item.type.value) ? item.due.value || null : null, note: item.note.value.trim() }));
    }
    dateField.input.addEventListener('input', preview);
    form.append(n('p', 'helper', '每次记录一个完整资产截面。未知余额留空，0 需显式填写。证券账户总额包含持仓，不再加上 P1 持仓。此快照不与原账本相加。'), dateField.wrapper, list, button('＋ 添加资产或负债', 'secondary wide', () => addRow(), 'snapshot-add-item'), summary, noteField.wrapper, error);
    const submit = n('button', 'primary wide', '保存完整快照'); submit.type = 'submit'; submit.id = 'save-snapshot'; form.append(submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); error.textContent = '';
      try {
        const input = { date: dateField.input.value, note: noteField.input.value.trim(), items: readItems() };
        if (replacing) input.replacesId = replacing;
        await commit(saveSnapshot(getState(), input), replacing ? '快照修订已保存，原始记录保留。' : '资产快照已保存到本机。'); box.close();
      } catch (issue) { fail(issue, error); }
    });
    box.append(form);
    return { open(snapshot) {
      replacing = snapshot?.id || null;
      $('snapshot-dialog-title').textContent = snapshot ? '修订资产快照' : '记录资产快照';
      dateField.input.value = snapshot?.date || today(); dateField.input.max = today();
      noteField.input.value = snapshot?.note || ''; error.textContent = '';
      itemRows = []; list.replaceChildren();
      const source = snapshot || latestSnapshot(getState(), today());
      if (source?.items?.length) source.items.forEach(item => addRow(item)); else addRow();
      preview(); box.showModal();
    } };
  }

  function openSalary(plan = null) {
    if (!salaryEditor) salaryEditor = makeSalaryDialog();
    salaryEditor.open(plan);
  }
  function makeSalaryDialog() {
    const box = dialog('salary-dialog', '工资 / 奖金到账安排');
    const form = n('form'); form.id = 'salary-form';
    const date = field('计划 / 到账日期', 'salary-date', { type: 'date', required: true });
    const receipt = amountField('这次工资 / 奖金（元）', 'salary-receipt'); receipt.input.required = true; receipt.wrapper.classList.add('amount-field');
    const cashBefore = amountField('到账前现金总额（元）', 'salary-cashBefore');
    const cashAsOf = field('现金余额核实日期', 'salary-cashAsOf', { type: 'date' });
    const risk = field('风险偏好', 'salary-risk', { options: [['conservative', '稳健'], ['balanced', '均衡'], ['growth', '进取']] });
    const conditions = n('details', 'investment-condition-details'); conditions.open = true;
    conditions.append(n('summary', '', '资金条件 · 未知请留空，不默认填零'));
    const fields = { receipt, cashBefore, cashAsOf, date, risk };
    const labels = { emergency: '应急储备目标（元）', fixed: '近期固定支出预留（元）', future: '已知未来支出预留（元）', repayment: '拟偿还本金（元）', debtBalance: '当前负债余额（元）', investment: '拟投入投资（元）', savings: '拟增加储蓄（元）', free: '拟留自由支配（元）' };
    const conditionGrid = n('div', 'investment-form-grid'); conditionGrid.append(cashBefore.wrapper, cashAsOf.wrapper);
    for (const key of ['emergency', 'fixed', 'future', 'repayment', 'debtBalance']) { fields[key] = amountField(labels[key], `salary-${key}`); conditionGrid.append(fields[key].wrapper); }
    fields.debtRate = field('负债年利率（%，无负债也请明确 0）', 'salary-debtRate', { placeholder: '未知请留空' }); fields.debtRate.input.inputMode = 'decimal';
    fields.debtDue = field('负债到期日（未知留空）', 'salary-debtDue', { type: 'date' });
    conditionGrid.append(fields.debtRate.wrapper, fields.debtDue.wrapper, risk.wrapper);
    conditions.append(conditionGrid);
    const allocationGrid = n('div', 'investment-form-grid');
    for (const key of ['investment', 'savings', 'free']) { fields[key] = amountField(labels[key], `salary-${key}`); allocationGrid.append(fields[key].wrapper); }
    const preview = n('div', 'inset-panel'); preview.id = 'salary-allocation-preview';
    const error = n('p', 'form-error'); error.id = 'salary-error'; error.setAttribute('role', 'alert');
    function readInput() {
      const input = { date: date.input.value, cashAsOf: cashAsOf.input.value || null, risk: risk.input.value, cashBasis: 'before-receipt', debtRate: fields.debtRate.input.value.trim() || null, debtDue: fields.debtDue.input.value || null };
      for (const key of ['receipt', 'cashBefore', ...Object.keys(labels)]) input[key] = parseMoney(fields[key].input.value);
      return input;
    }
    function assess() {
      try {
        const result = calculateAllocation(readInput());
        const content = [row('可供安排上限', money(result.available)), row('本次分配合计', money(result.total))];
        if (result.available === null) content.push(n('p', 'investment-warning', `其他现金未知；目前仅确认本次到账 ${money(result.knownMinimum ?? readInput().receipt)}。不能将它视为完整可用现金。`));
        for (const [key, title] of [['cash', '保留现金'], ['debt', '本金还款计划'], ['investment', '投资预算'], ['savings', '新增储蓄'], ['free', '自由支配']]) content.push(row(title, money(result.allocations?.[key])));
        for (const warning of result.warnings || []) content.push(n('p', 'investment-warning', warning));
        content.push(n('p', 'helper', result.canConfirm ? '条件完整，可保存为已确认计划。此操作不转账、不交易、不改变资产。' : '条件不足：可以保存草稿；补齐未知条件后才能确认计划。'));
        preview.replaceChildren(...content);
        $('salary-confirm-plan').disabled = !result.canConfirm;
        return result;
      } catch (issue) { preview.replaceChildren(n('p', 'helper', issue.message)); $('salary-confirm-plan').disabled = true; return null; }
    }
    form.append(n('p', 'helper', '先安排储备、支出与债务，再确定投资预算。没有默认买卖指令；这里只保存你的资金计划。现金余额必须是到账前数值，避免把收入加两次。'), receipt.wrapper, date.wrapper, button('载入最近快照的现金与日期', 'text-button', () => {
      const snapshot = latestSnapshot(getState(), today());
      if (!snapshot) { info('尚无资产快照，请先记录，或手动填写现金条件。'); return; }
      cashBefore.input.value = decimal(snapshotTotals(snapshot).cash); cashAsOf.input.value = snapshot.date; assess();
    }, 'salary-use-snapshot'), conditions, n('h3', '', '按你的意愿分配'), allocationGrid, n('p', 'helper', '金额单位为人民币元。填 0 表示明确不分配；留空表示尚未知。分配不能超过可用资金，保留现金由资金条件计算。'), preview, error);
    const saveDraft = n('button', 'primary wide', '保存草稿（不动钱）'); saveDraft.type = 'submit'; saveDraft.id = 'salary-save-draft';
    const confirmPlan = button('确认这份计划（不执行分配）', 'secondary wide', () => save('confirmed'), 'salary-confirm-plan');
    form.append(saveDraft, confirmPlan);
    async function save(status) {
      error.textContent = '';
      try {
        const input = readInput(); const result = calculateAllocation(input);
        if (status === 'confirmed') {
          if (!result.canConfirm) throw new Error('资金条件未完整，不可确认计划。');
          if (!confirm('确认仅保存这份计划，不会执行转账、还款或买卖。继续？')) return;
        }
        const data = { date: date.input.value, input, allocations: result.allocations, status };
        await commit(saveSalaryPlan(getState(), data), status === 'confirmed' ? '已保存确认计划；没有执行任何分配。' : '工资 / 奖金草稿已保存。'); box.close();
      } catch (issue) { fail(issue, error); }
    }
    form.addEventListener('submit', event => { event.preventDefault(); save('draft'); });
    form.addEventListener('input', assess); form.addEventListener('change', assess);
    box.append(form);
    return { open(plan) {
      const input = plan?.input || {};
      $('salary-dialog-title').textContent = plan ? '基于此计划另存修订' : '工资 / 奖金到账安排';
      for (const key of ['receipt', 'cashBefore', ...Object.keys(labels)]) fields[key].input.value = decimal(input[key]);
      date.input.value = plan?.date || today(); cashAsOf.input.value = input.cashAsOf || '';
      fields.debtRate.input.value = input.debtRate ?? ''; fields.debtDue.input.value = input.debtDue || '';
      risk.input.value = input.risk || 'conservative'; error.textContent = '';
      assess(); box.showModal();
    } };
  }

  function openReceipt(plan) {
    if (!receiptEditor) receiptEditor = makeReceiptDialog();
    receiptEditor.open(plan);
  }
  function makeReceiptDialog() {
    const box = dialog('salary-receipt-dialog', '确认工资已实际到账');
    const form = n('form'); form.id = 'salary-receipt-form';
    const message = n('p', 'helper');
    const snapshotChoice = field('以这份资产快照为基准', 'receipt-snapshot', { options: [] });
    const cashChoice = field('实际收到工资的现金 / 银行账户', 'receipt-cash-item', { options: [] });
    const error = n('p', 'form-error'); error.id = 'receipt-error'; error.setAttribute('role', 'alert');
    const confirmed = n('label', 'toggle-label'); const check = n('input'); check.type = 'checkbox'; check.id = 'receipt-confirm'; confirmed.append(check, document.createTextNode('我已核对：款项确实到账，基准快照中的现金是到账前余额。'));
    let selectedPlan;
    function updateAccounts() {
      const snapshot = snapshots().find(item => item.id === snapshotChoice.input.value);
      const options = (snapshot?.items || []).filter(item => ['bank', 'cash'].includes(item.type));
      cashChoice.input.replaceChildren(...options.map(item => { const option = n('option', '', `${item.name} · ${money(item.balance)}`); option.value = item.id; return option; }));
      if (!options.length) { const option = n('option', '', '该快照没有现金 / 银行账户'); option.value = ''; cashChoice.input.append(option); }
    }
    snapshotChoice.input.addEventListener('change', updateAccounts);
    const submit = n('button', 'primary wide', '核实到账，生成新快照'); submit.type = 'submit'; submit.id = 'confirm-salary-receipt';
    form.append(message, snapshotChoice.wrapper, cashChoice.wrapper, n('p', 'helper', '只将本次收入加到所选现金账户，并保留原快照。不执行计划中的投资、储蓄、还款或消费，也不新增旧账本收入。基准、日期不符或已确认过时会拒绝，请手动记录新快照。'), confirmed, error, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); error.textContent = '';
      try {
        if (!check.checked) throw new Error('请先核对到账和余额基准，并勾选确认。');
        if (!confirm(`仅将收入 ${money(selectedPlan.input.receipt)} 记入新资产快照，不执行资金分配。确认？`)) return;
        await commit(confirmSalaryReceipt(getState(), selectedPlan.id, { snapshotId: snapshotChoice.input.value, cashItemId: cashChoice.input.value }), '到账已记录为新快照，没有执行投资或还款。'); box.close();
      } catch (issue) { fail(issue, error); }
    });
    box.append(form);
    return { open(plan) {
      selectedPlan = plan; error.textContent = ''; check.checked = false;
      message.textContent = `计划日期 ${plan.date} · 待确认收入 ${money(plan.input.receipt)}。此按钮仅在你已经收到款项后使用。`;
      const options = activeSnapshots();
      snapshotChoice.input.replaceChildren(...options.map(item => { const option = n('option', '', `${item.date} · ${item.note || '完整资产快照'}`); option.value = item.id; return option; }));
      if (!options.length) { const option = n('option', '', '请先记录到账前资产快照'); option.value = ''; snapshotChoice.input.append(option); }
      updateAccounts(); box.showModal();
    } };
  }

  function openReportImport() {
    if (!reportEditor) reportEditor = makeReportDialog();
    reportEditor.open();
  }
  function loadLocalResearchReport() {
    if (!reportEditor) reportEditor = makeReportDialog();
    reportEditor.open();
    reportEditor.loadLocal();
  }
  function makeReportDialog() {
    const box = dialog('investment-report-dialog', '导入研究报告');
    const file = field('选择本机报告 JSON', 'investment-report-file', { type: 'file' }); file.input.accept = '.json,application/json';
    const preview = n('div', 'inset-panel hidden'); preview.id = 'investment-report-preview';
    const error = n('p', 'form-error'); error.id = 'investment-report-error'; error.setAttribute('role', 'alert');
    const loadStatus = n('p', 'helper'); loadStatus.id = 'investment-report-load-status'; loadStatus.setAttribute('role', 'status');
    let pending = null;
    let requestSequence = 0;
    let localRequest = null;
    function resetPreview() { pending = null; preview.classList.add('hidden'); submit.classList.add('hidden'); }
    async function validatePreview(text, token) {
      const doc = parseJsonStrict(text);
      await saveReport(getState(), doc);
      if (token !== requestSequence || !box.open) return;
      pending = doc; preview.replaceChildren(...reportDetails(doc, 'import-')); preview.classList.remove('hidden'); submit.classList.remove('hidden');
    }
    const submit = button('确认保存报告', 'primary wide hidden', async () => {
      try { if (!pending) throw new Error('请先选择并验证报告。'); await commit(await saveReport(getState(), pending), '研究报告已保存；未导入或改动真实持仓。'); box.close(); } catch (issue) { fail(issue, error); }
    }, 'investment-report-confirm');
    const read = button('验证并预览报告', 'secondary wide', async () => {
      error.textContent = ''; resetPreview(); loadStatus.textContent = ''; localRequest?.abort(); const token = ++requestSequence;
      try {
        const selected = file.input.files?.[0];
        if (!selected) throw new Error('请选择报告 JSON。');
        if (selected.size > 8 * 1024 * 1024) throw new Error('报告文件超过 8 MiB。');
        await validatePreview(await selected.text(), token);
      } catch (issue) { if (token === requestSequence) fail(issue, error); }
    }, 'investment-report-preview-button');
    file.input.addEventListener('change', () => { requestSequence++; localRequest?.abort(); resetPreview(); loadStatus.textContent = ''; error.textContent = ''; });
    box.addEventListener('close', () => { requestSequence++; localRequest?.abort(); resetPreview(); });
    box.append(n('p', 'helper', '只读取你明确选择的文件，或由你点击载入本机生成的公共研究报告。来源与日期不代表内容已经核实；不扫描磁盘，不调用 AI，不导入真实券商数据。预览后仍需确认才保存，不同步个人记录。'), file.wrapper, read, loadStatus, preview, error, submit);
    return {
      open() { requestSequence++; localRequest?.abort(); resetPreview(); file.input.value = ''; error.textContent = ''; loadStatus.textContent = ''; if (!box.open) box.showModal(); },
      async loadLocal() {
        const token = ++requestSequence; localRequest?.abort(); localRequest = new AbortController();
        const controller = localRequest;
        resetPreview(); error.textContent = ''; loadStatus.textContent = '正在读取 Windows 生成的本轮研究报告，尚未保存…';
        const timer = setTimeout(() => controller.abort(), 15000);
        let downloaded = false;
        try {
          const response = await fetch('./research-report-v32.json', { cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal, headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('本机研究报告暂不可用');
          if (Number(response.headers.get('Content-Length') || 0) > 8 * 1024 * 1024) throw new Error('研究报告超过 8 MiB');
          const text = await response.text(); downloaded = true;
          if (token !== requestSequence || !box.open) return;
          await validatePreview(text, token);
          if (token === requestSequence) loadStatus.textContent = '已载入本机公共行情 / 独立测试账户研究报告。请核对下方来源和数据日期；确认后才保存。未同步个人财富记录。';
        } catch (issue) {
          if (token !== requestSequence || !box.open) return;
          resetPreview(); loadStatus.textContent = '';
          fail(new Error(downloaded ? `报告校验未通过：${issue.message}。未使用旧报告替代。` : '请在 Windows 先运行研究；离线可导入已下载文件。未使用旧报告替代。'), error);
        } finally { clearTimeout(timer); if (localRequest === controller) localRequest = null; }
      },
    };
  }

  function reportDetails(report, prefix = '') {
    const assessment = reportAssessment(report, today());
    const out = [n('p', 'investment-report-source', `${report.source} · 数据日期 ${report.asOf} · ${assessment.stale ? '已陈旧，需重新核实' : '仍需独立核实'}`), n('p', 'helper', `策略：${report.strategy} · 生成时间 ${report.generatedAt}`)];
    for (const warning of assessment.warnings || []) out.push(n('p', 'investment-warning', warning));
    if (report.research) out.push(researchDetails(report, prefix));
    if (!report.signals?.length) out.push(n('p', 'investment-warning', '缺少信号：无法从这份报告形成标的判断。'));
    for (const signal of report.signals || []) {
      const card = n('section', 'investment-signal');
      card.append(n('h3', '', `${signal.symbol} · ${report.research ? '历史研究标签：' : ''}${STANCES[signal.stance] || '未知判断'}`), n('p', 'helper', `证据质量（来源自评，未独立复算）：${QUALITY[signal.quality] || '未知'}。依据：${signal.basis || '缺失'}。`), n('p', 'investment-warning', `风险：${signal.risk || '缺失，不能视为无风险'}`));
      out.push(card);
    }
    const backtest = n('details', 'extra-details'); backtest.append(n('summary', '', '回测证据、费用与局限'));
    if (!report.backtest) backtest.append(n('p', 'investment-warning', '缺少回测数据、手续费与滑点证据，无法据此推断收益。'));
    else {
      const value = report.backtest;
      backtest.append(n('p', 'helper', `${value.from} 至 ${value.to} · ${report.research ? '全区间价格路径 TWR' : '来源声明收益'} ${bps(value.returnBps)} · 最大回撤 ${bps(value.maxDrawdownBps)} · ${value.tradeCount} 笔模拟交易`), n('p', 'helper', `佣金 ${bps(value.commissionBps)} · 滑点 ${bps(value.slippageBps)}。历史与回测不保证未来结果。`), n('p', 'investment-warning', `局限：${value.limitations || '未说明'}`));
    }
    out.push(backtest);
    const trades = n('details', 'extra-details'); const researchCount=report.research?.comparison.find(x=>x.strategy==='v1_ma20')?.tradeCount; trades.append(n('summary', '', report.research ? `模拟成交详情（研究 ${researchCount ?? '未知'} 笔，逐笔见本机文件）` : `模拟交易历史（${report.paperTrades?.length || 0} 笔）`));
    if (!report.paperTrades?.length) trades.append(n('p', 'helper', report.research ? '手机报告未嵌入分以下精度的逐笔成交价。各策略完整模拟指令、成交和费用保存在独立 backtest.json；不是零成交，也不是真实券商成交。' : '缺少模拟交易历史；未连接券商。'));
    else for (const trade of report.paperTrades) trades.append(n('p', 'helper', `${trade.date} · ${trade.symbol} · ${trade.side === 'buy' ? '模拟买入' : '模拟卖出'} ${trade.quantity} · 单价 ${money(trade.price)} · 总成本 ${money(trade.totalCost)}`));
    out.push(trades);
    if (report.notes) { if(report.research){ const notes=n('details','extra-details');notes.append(n('summary','','报告附注与完整研究口径'),n('p','helper',report.notes));out.push(notes); } else out.push(n('p', 'helper', report.notes)); }
    return out;
  }
  function bps(value) {
    if (!Number.isSafeInteger(value)) return '未知';
    const integer = BigInt(value), absolute = integer < 0n ? -integer : integer;
    return `${integer < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}%`;
  }

  function researchDetails(report, prefix = '') {
    const research = report.research, dataset = research.dataset;
    const root = n('section'); root.dataset.researchVersion = String(research.version);
    const health = n('div', 'notice warning'); health.id = `${prefix}research-health-banner`; health.setAttribute('role', 'status');
    const healthTitles = { synthetic: '虚构数据 · 仅验证研究流程', blocked: '数据检查阻断 · 禁止用于决策', validated: '历史数据研究 · 不生成实时交易建议' };
    health.append(n('strong', '', healthTitles[dataset.quality]), n('p', '', `决策权限：已禁用。${dataset.crossValidated ? '报告声明已交叉核对，本应用未独立复算。' : '单源数据，未完成独立来源交叉核对。'}${dataset.unitVerified ? '' : '价格单位未经核验，不能解释为真实价格。'}`));
    if (research.health.codes.length) health.append(n('p', '', `诊断码：${research.health.codes.join(' · ')}`));
    if (research.reportKind === 'pre1445-test') health.append(n('p', '', `14:45 生成流程测试：${research.generatedBeforeCutoff ? '报告声明并通过时间口径检查为中国时间14:45前生成' : '未通过提前生成条件'}；不执行交易。`));
    root.append(health);
    const provenance = n('details', 'extra-details'); provenance.id = `${prefix}research-provenance`; provenance.append(n('summary', '', '数据来源、截止日期与价格口径'));
    provenance.append(n('p', 'helper', `数据来源：${dataset.source}；报告发布来源：${report.source}。两者分别记录，不等同于独立核验。`), n('p', 'helper', `来源网址：${dataset.sourceUrl}`), n('p', 'helper', `数据截止：${dataset.through} · 不复权（none） · 人民币元 / 股或份（CNY-per-share）`), n('p', 'helper', `数据 SHA256：${dataset.sha256}`), n('p', 'helper', '数据指纹用于核对文件身份，不能单独证明价格真实。历史收益为价格研究，不应当作含分红的全收益。'));
    root.append(provenance);
    const comparison = n('section', 'inset-panel'); comparison.id = `${prefix}research-comparison`;
    comparison.append(n('h3', '', '四策略 · 同区间比较'), n('p', 'helper', `共同区间 ${research.split.train.from} 至 ${research.split.test.to}。以买入持有为基准；展开各策略查看年化、回撤、波动、成交与实际模拟费用。`));
    comparison.append(n('p','helper','价格路径 TWR：扣模拟成本，不含分红；共同资金及注资假设，不是完整含息总回报。'));
    const names = { buy_hold: '买入持有', v1_ma20: '原 V1 MA20', ma20_cross: 'MA20 穿越', periodic: '定期买入' };
    for (const item of researchComparison(report)) {
      const section = n('details', 'extra-details'); section.dataset.strategy = item.strategy;
      section.append(n('summary', '', `${names[item.strategy]} · 总收益 ${bps(item.totalReturnBps)} · 相对基准 ${bps(item.excessReturnBps).replace('%', ' 个百分点')}`));
      section.append(row('年化收益', bps(item.annualizedReturnBps)), row('最大回撤', bps(item.maxDrawdownBps)), row('年化波动率', bps(item.volatilityBps)), row('成交次数', item.tradeCount), row('模拟费用合计', money(item.cost)), row('费用相对基准差额', money(item.costDifference)));
      comparison.append(section);
    }
    comparison.append(n('p', 'helper', '费用和费用差额按整数分精确计算。基点差是同区间历史指标之差，不是未来超额收益预测。'));
    root.append(comparison);
    const split = n('details', 'extra-details'); split.id = `${prefix}research-split`; split.append(n('summary', '', '训练、验证与测试区间'));
    for (const [key, title] of [['train', '训练'], ['validation', '验证'], ['test', '测试']]) split.append(row(title, `${research.split[key].from} — ${research.split[key].to}`));
    split.append(n('p', 'helper', '四策略上方指标覆盖完整共同区间，不能冒充单独测试集表现。分段区间有序、不重叠，未证明未来收益。')); root.append(split);
    const limitations = n('details', 'extra-details'); limitations.id = `${prefix}research-limitations`; limitations.append(n('summary', '', `研究局限与缺失（${research.limitations.length} 项）`));
    for (const limitation of research.limitations) limitations.append(n('p', 'investment-warning', limitation));
    root.append(limitations); return root;
  }

  async function fictionalReport() {
    if (!confirm('将添加一份明确虚构的研究报告，仅用于体验报告与模拟历史展示；不会创建持仓或任何交易。确认？')) return;
    const date = today();
    const doc = { format: 'goose-investment-report', version: 1, source: '完全虚构的体验报告 · 非真实行情', asOf: date, generatedAt: new Date().toISOString(), strategy: '虚构示例：先检查证据，再作观察', symbols: ['600900.SH', '515450.SH'], signals: [{ symbol: '600900.SH', stance: 'hold', basis: '虚构：仅演示持有情景文本，无行情或财务依据。', risk: '虚构报告不能作为买卖依据，真实风险尚未研究。', quality: 'low' }, { symbol: '515450.SH', stance: 'watch', basis: '虚构：历史行情尚未核验，保持观察。', risk: '价格、成本、流动性与跟踪误差未知。', quality: 'low' }], backtest: null, paperTrades: [{ date, symbol: '600900.SH', side: 'buy', quantity: 100, price: '100', totalCost: '10100' }], notes: '所有数字完全虚构，模拟交易仅演示界面，不构成真实持仓。缺少真实回测证据，不给出当前价格、盈亏或交易建议。' };
    try { await commit(await saveReport(getState(), doc), '已保存明确虚构的报告示例。'); } catch (issue) { fail(issue); }
  }

  function renderInvestment() {
    const latest = latestSnapshot(getState(), today());
    const hero = n('article', 'investment-hero');
    const top = n('div', 'section-heading'); top.append(n('span', 'eyebrow', '完整资产快照'), button('更新资产', 'investment-hero-action', () => openSnapshot(), 'investment-new-snapshot'));
    hero.append(top, n('p', 'investment-hero-caption', latest ? `最近核实 · ${latest.date}` : '先记录一份完整资产快照'));
    const totals = latest ? snapshotTotals(latest) : null;
    hero.append(n('p', 'investment-hero-money', totals ? money(totals.netWorth) : '尚未记录'));
    const split = n('div', 'investment-overview-split');
    split.append(row('资产', totals ? money(totals.assets) : '未知'), row('负债', totals ? money(totals.liabilities) : '未知'), row('现金', totals ? money(totals.cash) : '未知'));
    hero.append(split, n('p', 'investment-hero-note', '快照、账本、历史持仓分别保存，不重复相加。留空的金额始终未知。'));
    if (latest?.date < today()) hero.append(n('p', 'investment-hero-note', '快照不是今日实时余额；安排资金前请重新核实。'));
    $('investment-overview').replaceChildren(hero);

    const allocation = panel('工资 / 奖金安排', button('＋ 安排一笔', 'text-button', () => openSalary(), 'new-salary-plan'));
    allocation.append(n('p', 'helper', '收入先进入计划，再考虑储备、负债与投资。保存计划不会动钱。'));
    const salaryPlans = plans().slice().sort((a, b) => b.date.localeCompare(a.date) || (b.createdAt || '').localeCompare(a.createdAt || ''));
    if (!salaryPlans.length) allocation.append(n('p', 'empty-list', '还没有工资或奖金计划。填入金额即可开始，未知条件可先存草稿。'));
    for (const plan of salaryPlans.slice(0, 5)) {
      const item = n('section', 'investment-plan-item'); item.dataset.planId = plan.id;
      const receiptDone = !!(plan.receiptConfirmed || plan.receiptSnapshotId || plan.receiptApplied || plan.receiptConfirmation || plan.receiptConfirmedAt);
      item.append(row(`${plan.date} · ${plan.status === 'confirmed' ? '已确认计划' : '草稿'}`, money(plan.input?.receipt)));
      const controls = n('div', 'button-row'); controls.append(button('查看 / 另存修订', 'text-button', () => openSalary(plan)));
      if (plan.status === 'confirmed') controls.append(button(receiptDone ? '收入已记录为快照' : '我已收到款项，核实入账', 'text-button', receiptDone ? () => info('这笔收入已记录，不会重复加到现金。') : () => openReceipt(plan)));
      item.append(controls, n('p', 'helper', '计划分配未执行；到账确认也只记收入快照。')); allocation.append(item);
    }
    $('allocation-plans').replaceChildren(allocation);
    renderResearch(); renderSnapshots();
  }

  function renderSnapshots() {
    const section = panel('完整资产快照', button('＋ 记录快照', 'text-button', () => openSnapshot(), 'assets-new-snapshot'));
    section.append(n('p', 'helper', '只需定期更新账户余额，不必为每笔小额消费记账。每份快照独立完整，修订与撤销保留痕迹。'));
    const list = activeSnapshots();
    if (!list.length) section.append(n('p', 'empty-list', '尚无资产快照，原有账本数据完整保留。'));
    if (list.length) {
      const history = n('div', 'inset-panel investment-networth-history'); history.id = 'snapshot-networth-history';
      history.append(n('h3', '', '最近净资产变化 · 按快照日期'));
      const byDate = new Map();
      for (const snapshot of list) if (!byDate.has(snapshot.date)) byDate.set(snapshot.date, snapshot);
      for (const snapshot of [...byDate.values()].slice(0, 7)) history.append(row(snapshot.date, money(snapshotTotals(snapshot).netWorth)));
      history.append(n('p', 'helper', '仅为各日已记录快照，非每日实时估值；未知金额不补零。')); section.append(history);
    }
    for (const snapshot of list.slice(0, 30)) {
      const item = n('section', 'investment-snapshot-record'); item.dataset.snapshotId = snapshot.id;
      const totals = snapshotTotals(snapshot);
      item.append(row(snapshot.date, `净资产 ${money(totals.netWorth)}`), n('p', 'helper', snapshot.note || '无备注'));
      const details = n('details', 'extra-details'); details.append(n('summary', '', `查看 ${snapshot.items.length} 个账户`));
      for (const account of snapshot.items) { details.append(row(`${account.name} · ${TYPES[account.type]}`, money(account.balance))); if (debt(account.type)) details.append(n('p', 'helper', `年利率：${account.annualRate === null ? '未知' : `${account.annualRate}%`} · 到期日：${account.dueDate || '未知'}`)); }
      const controls = n('div', 'button-row'); controls.append(button('修订', 'text-button', () => openSnapshot(snapshot)), button('撤销快照', 'text-button danger-text', async () => { if (!confirm(`撤销 ${snapshot.date} 的快照？原始记录会保留，不修改账本或 P1 数据。`)) return; try { await commit(voidSnapshot(getState(), snapshot.id), '快照已撤销，原始记录保留。'); } catch (issue) { fail(issue); } }));
      item.append(details, controls); section.append(item);
    }
    const past = snapshots().filter(item => !list.some(active => active.id === item.id)).sort((a, b) => b.date.localeCompare(a.date));
    if (past.length) {
      const history = n('details', 'extra-details'); history.append(n('summary', '', `已修订 / 撤销的原始快照（${past.length} 份）`));
      for (const snapshot of past.slice(0, 50)) history.append(n('p', 'helper', `${snapshot.date} · ${snapshot.voided ? '已撤销' : '已被修订替代'} · 净资产 ${money(snapshotTotals(snapshot).netWorth)} · ${snapshot.note || '无备注'}`));
      history.append(n('p', 'helper', '原始记录保持在完整加密备份中，此处不删除历史。')); section.append(history);
    }
    $('snapshot-list').replaceChildren(section);
  }

  function renderResearch() {
    const panelElement = panel('今日研究与决策', button('导入报告', 'text-button', openReportImport, 'open-investment-report'));
    panelElement.append(button('载入本机研究报告', 'secondary wide', loadLocalResearchReport, 'load-local-research-report'), n('p', 'helper', '仅点击时从本机服务载入本轮公共研究报告，先预览再确认。不自动同步个人数据；离线可使用已导入报告或手动导入下载文件。'));
    const daily = buildDailyDecision(getState(), today());
    const dailyStatus = { missing: '缺少报告', historical: '历史报告待更新', 'source-unverified': '报告来源待核验' };
    const decision = n('div', 'investment-daily-decision'); decision.id = 'daily-decision-summary';
    decision.append(n('p', 'investment-report-source', `${daily.date} · ${dailyStatus[daily.status] || '数据待核实'}`));
    for (const warning of daily.warnings || []) decision.append(n('p', 'investment-warning', warning));
    decision.append(n('p', 'helper', daily.action || '先核实数据；本报告不执行交易'));
    decision.append(button('导出今日决策记录', 'secondary wide', () => {
      if (!confirm('今日决策 JSON 包含个人财务内容，将以未加密文件下载到本机。请自行保管，不会自动上传或执行交易。确认导出？')) return;
      try { download(buildDailyDecision(getState(), today()), `Goose-daily-decision-${today()}.json`); info('今日决策记录已在本机生成，没有上传或执行交易。'); } catch (issue) { fail(issue); }
    }, 'export-daily-decision'));
    panelElement.append(decision);
    const reports = arrays('reports').slice().sort((a, b) => String(b.asOf).localeCompare(String(a.asOf)) || String(b.generatedAt).localeCompare(String(a.generatedAt)));
    const report = reports.find(item => item.id === selectedReportId) || reports[0];
    if (reports.length > 1) {
      const selector = field('查看已导入报告', 'investment-report-selection', { value: report.id, options: reports.map(item => [item.id, `${item.asOf} · ${item.source}`]) });
      selector.input.addEventListener('change', () => { selectedReportId = selector.input.value; renderResearch(); }); panelElement.append(selector.wrapper);
    }
    if (!report) panelElement.append(n('p', 'investment-empty-report', '暂无可用报告。缺少当前价格、实时持仓成本与有效研究证据，今天先核实资产和资金条件。'));
    else panelElement.append(...reportDetails(report));
    const boundaries = n('details', 'investment-boundaries'); boundaries.append(n('summary', '', '四种情景的边界'));
    for (const [heading, text] of [['持有', '只在原有投资逻辑与风险承受能力仍成立时评估；历史快照不能证明今天应继续持有。'], ['观察', '数据陈旧、来源未核验、成本或现金未知时，先补证据，不默认需要交易。'], ['分批买入', '只有预算、流动性、风险与证据都明确后才研究；本应用不执行买入，也不把回测当未来收益。'], ['减仓', '先核实原逻辑变化、集中度与现金需求，再考虑费用与税费；不凭单个信号自动减仓。']]) boundaries.append(n('p', 'helper', `${heading}：${text}`));
    panelElement.append(boundaries);
    const watchDetails = n('details', 'investment-boundaries'); watchDetails.id = 'investment-watch-details'; watchDetails.append(n('summary', '', '关注池与添加关注 · 不是持仓'));
    const watchlist = arrays('watchlist');
    const suggestions = n('div', 'investment-watch-suggestions');
    for (const candidate of [{ symbol: '600900.SH', name: '长江电力' }, { symbol: '515450.SH', name: '红利低波 ETF' }].filter(item => !watchlist.some(watch => watch.symbol === item.symbol))) suggestions.append(button(`＋ 关注 ${candidate.name} · ${candidate.symbol}`, 'secondary', async () => { try { await commit(addWatch(getState(), candidate), '已添加关注；不代表真实持仓或买入建议。'); } catch (issue) { fail(issue); } }));
    if (suggestions.childElementCount) { watchDetails.append(n('p', 'helper', '可选关注入口：只有点击后才加入关注池，不自动创建持仓。'), suggestions); }
    for (const item of watchlist) { const watchRow = n('div', 'investment-watch-row'); watchRow.append(n('span', '', `${item.name || item.symbol} · ${item.symbol}`), button('移除', 'text-button danger-text', async () => { try { await commit(removeWatch(getState(), item.id), '已从关注池移除，未改动持仓。'); } catch (issue) { fail(issue); } })); watchDetails.append(watchRow); }
    const watchForm = n('form', 'investment-watch-form'); watchForm.id = 'watch-form';
    const symbol = field('证券代码', 'watch-symbol', { required: true, placeholder: '例如：600900.SH' });
    const name = field('关注名称', 'watch-name', { required: true, placeholder: '仅关注，不创建持仓' });
    const submit = n('button', 'secondary', '添加关注'); submit.type = 'submit';
    watchForm.append(symbol.wrapper, name.wrapper, submit); watchForm.addEventListener('submit', async event => { event.preventDefault(); try { await commit(addWatch(getState(), { symbol: symbol.input.value.trim().toUpperCase(), name: name.input.value.trim() }), '已添加关注，未创建持仓。'); } catch (issue) { fail(issue); } }); watchDetails.append(watchForm); panelElement.append(watchDetails);
    const imported = getState().imports || [];
    const historical = n('details', 'extra-details'); historical.append(n('summary', '', `已有 P1 历史参考（${imported.length} 份）`));
    if (!imported.length) historical.append(n('p', 'helper', '未导入。可在资产页手动选择 P1 历史快照，绝不自动读取。'));
    for (const item of imported) { historical.append(n('p', 'helper', `${item.source} · ${item.asOf}`)); for (const position of item.positions || []) historical.append(row(position.symbol, `${position.quantity} 股 / 份 · 历史数量`)); }
    historical.append(n('p', 'investment-warning', '历史数量不是实时持仓。515450 未核验历史行情不用于当前市值、盈亏或买卖判断。')); panelElement.append(historical);
    const actions = n('div', 'button-row'); actions.append(button('体验一份明确虚构的报告', 'text-button', fictionalReport, 'load-fictional-report'), button('导出 AI 复核资料', 'text-button', () => {
      try { if (!confirm('将本机资产与研究内容导出为未加密 JSON。请自行保管；不会自动发送给 AI 或任何服务。继续导出？')) return; download(buildReviewPackage(getState(), today()), `Goose-review-${today()}.json`); info('复核资料只下载到本机，没有自动调用或上传给 AI。'); } catch (issue) { fail(issue); }
    }, 'export-review-package'));
    panelElement.append(actions, n('p', 'helper', 'AI 复核资料将事实、推测与建议分区；导出不代表已获得 AI 判断。真实财富数据只保存在本机，没有 Windows 双向同步。'));
    $('investment-research').replaceChildren(panelElement);
  }
  return { render: renderInvestment };
}

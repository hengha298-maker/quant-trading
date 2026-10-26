// V3.2 planning is separate from the optional ledger. All money is integer CNY cents.
const TYPES = new Set(['bank','cash','securities','fund','other','loan','credit']);
const DEBTS = new Set(['loan','credit']);
const CASH = new Set(['bank','cash']);
const LISTS = ['snapshots','reports','watchlist','salaryPlans'];
const fail = message => { throw new Error(message); };
const copy = value => structuredClone(value);
const uid = () => crypto.randomUUID();
export const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const now = () => new Date().toISOString();
function obj(x, label) { if (!x || typeof x !== 'object' || Array.isArray(x) || ![Object.prototype,null].includes(Object.getPrototypeOf(x))) fail(`${label}格式无效`); }
function str(x,label,max=2000) { if (typeof x !== 'string' || x.length>max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(x)) fail(`${label}格式无效`); }
function id(x) { str(x,'编号',128); if (!x.trim() || ['__proto__','prototype','constructor'].includes(x)) fail('编号无效'); }
function date(x) { if (typeof x!=='string' || !/^\d{4}-\d{2}-\d{2}$/.test(x) || !Number.isFinite(Date.parse(x)) || new Date(x).toISOString().slice(0,10)!==x) fail('日期无效'); return x; }
function stamp(x) { if (typeof x!=='string' || !Number.isFinite(Date.parse(x)) || new Date(x).toISOString()!==x) fail('时间戳无效'); }
function amount(x,nullable=false) { if (nullable && x===null) return null; if (typeof x!=='string' || !/^(?:0|[1-9]\d{0,59})$/.test(x)) fail('金额必须为非负整数分或明确未知'); return BigInt(x); }
function rate(x) { if (x!==null && (typeof x!=='string' || !/^(?:0|[1-9]\d{0,2})(?:\.\d{1,6})?$/.test(x) || Number(x)>100)) fail('年利率必须在0–100%之间或未知'); }
function symbol(x) { if (typeof x!=='string' || !/^\d{6}\.(SH|SZ)$/.test(x)) fail('标的格式应为600900.SH或六位代码加.SH/.SZ'); }
function exactKeys(x,keys,label) { obj(x,label); if (Object.keys(x).some(k=>!keys.includes(k))) fail(`${label}含不支持的字段`); }
function safe(x,depth=0) { if(depth>25) fail('规划数据嵌套过深'); if(x===null || ['string','boolean'].includes(typeof x)) return; if(typeof x==='number' && Number.isSafeInteger(x)) return; if(Array.isArray(x)){for(const v of x)safe(v,depth+1);return;} obj(x,'规划数据'); for(const [k,v] of Object.entries(x)){if(['__proto__','constructor','prototype'].includes(k))fail('不安全字段');safe(v,depth+1);} }
const canonical = x => Array.isArray(x)?`[${x.map(canonical).join(',')}]`:x && typeof x==='object'?`{${Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')}}`:JSON.stringify(x);

export function emptyPlanning() { return {schemaVersion:1,snapshots:[],reports:[],watchlist:[],salaryPlans:[]}; }
function withPlanning(state) { const next=copy(state); if(!Object.hasOwn(next,'planning'))next.planning=emptyPlanning(); validatePlanning(next.planning); return next; }
function validateItems(items) {
  if(!Array.isArray(items)||!items.length||items.length>200)fail('快照需1–200个账户'); const ids=new Set();
  for(const x of items){exactKeys(x,['id','name','type','balance','annualRate','dueDate','note'],'快照账户');id(x.id);if(ids.has(x.id))fail('快照账户编号重复');ids.add(x.id);str(x.name,'账户名称',100);if(!x.name.trim()||!TYPES.has(x.type))fail('快照账户名称或类型无效');amount(x.balance,true);rate(x.annualRate);if(x.dueDate!==null)date(x.dueDate);str(x.note,'账户备注');}
  for(const kinds of [new Set(['bank','cash','securities','fund','other']),DEBTS,CASH]){let total=0n;for(const x of items)if(kinds.has(x.type)&&x.balance!==null)total+=BigInt(x.balance);amount(total.toString());}
}
function validateSnapshot(x){exactKeys(x,['id','date','items','note','createdAt','voided','replacesId'],'快照');id(x.id);date(x.date);if(x.date>localToday())fail('不能把未来快照当成当前余额');validateItems(x.items);str(x.note,'快照备注');stamp(x.createdAt);if(typeof x.voided!=='boolean')fail('快照撤销标记无效');if(x.replacesId!==null)id(x.replacesId);}
const INPUT_MONEY=['receipt','cashBefore','emergency','fixed','future','investment','savings','free','repayment','debtBalance'];
function validateAllocationInput(x){exactKeys(x,[...INPUT_MONEY,'date','cashAsOf','debtRate','debtDue','risk','cashBasis'],'资金条件');for(const key of INPUT_MONEY)amount(x[key],key!=='receipt');if(amount(x.receipt)<=0n)fail('工资奖金须大于零');date(x.date);if(x.cashAsOf!==null)date(x.cashAsOf);rate(x.debtRate);if(x.debtDue!==null)date(x.debtDue);if(!['conservative','balanced','growth'].includes(x.risk)||x.cashBasis!=='before-receipt')fail('风险偏好或现金口径无效');}
function checkAllocations(x,available){exactKeys(x,['cash','debt','investment','savings','free'],'分配方案');let sum=0n;for(const key of ['cash','debt','investment','savings','free'])sum+=amount(x[key]);if(sum>available)fail('分配合计超过实际可分配金额');return sum;}
const RESEARCH_STRATEGIES = ['buy_hold','v1_ma20','ma20_cross','periodic'];
function requiredKeys(x, keys, label) { exactKeys(x, keys, label); if (keys.some(key => !Object.hasOwn(x, key))) fail(`${label}缺少必填字段`); }
function researchInteger(value, label, min, max) { if (!Number.isSafeInteger(value) || value < min || value > max) fail(`${label}超出整数范围`); }
function nonempty(value, label, max = 2000) { str(value, label, max); if (!value.trim()) fail(`${label}不能为空`); }
function validateResearch(research, report) {
  requiredKeys(research, ['version','dataset','comparison','split','limitations','reportKind','generatedBeforeCutoff','decisionAllowed','health'], '研究扩展');
  if (research.version !== 1 || research.decisionAllowed !== false || typeof research.generatedBeforeCutoff !== 'boolean' || !['historical-research','pre1445-test'].includes(research.reportKind)) fail('研究扩展版本、生成类型或决策禁用标记无效');
  const dataset = research.dataset;
  requiredKeys(dataset, ['source','sourceUrl','through','adjustment','priceUnit','quality','crossValidated','unitVerified','sha256'], '研究数据来源');
  nonempty(dataset.source, '数据来源', 300); nonempty(dataset.sourceUrl, '数据来源网址', 2000);
  let sourceUrl; try { sourceUrl = new URL(dataset.sourceUrl); } catch { fail('数据来源网址无效'); }
  if (sourceUrl.protocol !== 'https:' || sourceUrl.username || sourceUrl.password || !sourceUrl.hostname || sourceUrl.hash || /[\u0000-\u0020\u007f]/.test(dataset.sourceUrl)) fail('数据来源须为不含身份凭据或空白的 HTTPS 网址');
  if (dataset.adjustment !== 'none' || dataset.priceUnit !== 'CNY-per-share' || !['validated','synthetic','blocked'].includes(dataset.quality) || typeof dataset.crossValidated !== 'boolean' || typeof dataset.unitVerified !== 'boolean' || typeof dataset.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(dataset.sha256)) fail('复权口径、价格单位、质量或数据指纹无效');
  date(dataset.through); if (dataset.through > report.asOf) fail('数据截止日期不能晚于报告日期');
  const generatedDay = new Date(Date.parse(report.generatedAt) + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  if (dataset.through > generatedDay || report.asOf > generatedDay) fail('研究日期不能包含报告生成时尚未发生的未来日期');
  if (dataset.quality === 'validated' && !dataset.unitVerified) fail('单位未经核验的数据不能标为 validated');
  if (dataset.quality !== 'synthetic' && /(?:^|\.)invalid$/.test(sourceUrl.hostname)) fail('非虚构数据不能使用虚构来源网址');
  const health = research.health;
  requiredKeys(health, ['status','codes'], '研究健康状态');
  const expectedHealth = { validated: 'historical', synthetic: 'synthetic', blocked: 'blocked' }[dataset.quality];
  if (health.status !== expectedHealth || !Array.isArray(health.codes) || health.codes.length > 100 || new Set(health.codes).size !== health.codes.length) fail('研究健康状态与数据质量不一致');
  for (const code of health.codes) nonempty(code, '研究诊断码', 200);
  if (health.status !== 'historical' && !health.codes.length) fail('虚构或阻断数据须说明诊断码');
  if (dataset.quality !== 'validated' && dataset.crossValidated) fail('虚构或阻断数据不能宣称真实来源交叉核验通过');
  if ((dataset.quality !== 'validated' || !dataset.crossValidated || !dataset.unitVerified) && report.signals.some(signal => signal.quality === 'high')) fail('缺少真实来源交叉核验，禁止高置信信号声明');
  if (dataset.quality !== 'validated' && report.signals.some(signal => signal.stance !== 'watch' || signal.quality !== 'low')) fail('虚构或阻断数据只能生成低质量观察标签，不能生成买卖方向信号');
  requiredKeys(research.split, ['train','validation','test'], '研究分段');
  let previous = null;
  for (const key of ['train','validation','test']) {
    const span = research.split[key]; requiredKeys(span, ['from','to'], `${key}区间`); date(span.from); date(span.to);
    if (span.from > span.to || span.to > dataset.through || previous !== null && span.from <= previous) fail('训练、验证、测试区间必须有序且不重叠，不能越过数据截止日');
    previous = span.to;
  }
  if (report.backtest && (report.backtest.from !== research.split.train.from || report.backtest.to !== research.split.test.to)) fail('顶层回测与四策略比较须使用同一完整研究区间');
  if (!Array.isArray(research.comparison) || research.comparison.length !== 4) fail('同区间比较必须包含完整四个策略');
  const seen = new Set();
  for (const item of research.comparison) {
    requiredKeys(item, ['strategy','totalReturnBps','annualizedReturnBps','maxDrawdownBps','volatilityBps','tradeCount','cost'], '比较指标');
    if (!RESEARCH_STRATEGIES.includes(item.strategy) || seen.has(item.strategy)) fail('比较策略无效或重复'); seen.add(item.strategy);
    researchInteger(item.totalReturnBps, '总收益基点', -10000, 1000000000);
    researchInteger(item.annualizedReturnBps, '年化收益基点', -10000, 1000000000);
    researchInteger(item.maxDrawdownBps, '最大回撤基点', 0, 10000);
    researchInteger(item.volatilityBps, '波动率基点', 0, 1000000000);
    researchInteger(item.tradeCount, '成交次数', 0, 10000000); amount(item.cost);
  }
  if (!Array.isArray(research.limitations) || !research.limitations.length || research.limitations.length > 100) fail('研究扩展须说明样本与方法局限');
  for (const limitation of research.limitations) nonempty(limitation, '研究局限', 4000);
  if (research.generatedBeforeCutoff) {
    const shanghai = new Date(Date.parse(report.generatedAt) + 8 * 60 * 60 * 1000).toISOString();
    if (shanghai.slice(11, 16) >= '14:45') fail('生成时间不在中国时间14:45前，不能声明提前生成');
  }
}

// Comparison costs and differences remain exact integer cents; percentages are integer basis points.
export function researchComparison(report) {
  if (!report.research) return [];
  validateResearch(report.research, report);
  const benchmark = report.research.comparison.find(item => item.strategy === 'buy_hold');
  return RESEARCH_STRATEGIES.map(strategy => {
    const item = report.research.comparison.find(value => value.strategy === strategy);
    return { ...copy(item), excessReturnBps: item.totalReturnBps - benchmark.totalReturnBps, costDifference: (BigInt(item.cost) - BigInt(benchmark.cost)).toString() };
  });
}
function validatePlan(x){exactKeys(x,['id','date','input','allocations','status','createdAt','receiptApplied'],'工资奖金计划');id(x.id);date(x.date);validateAllocationInput(x.input);if(x.date!==x.input.date)fail('计划日期不一致');const p=calculateAllocation(x.input);checkAllocations(x.allocations,BigInt(p.budgetCap));if(!['draft','confirmed'].includes(x.status))fail('计划状态无效');if(x.status==='confirmed'&&(INPUT_MONEY.some(k=>x.input[k]===null)||x.input.cashAsOf!==x.input.date||(BigInt(x.input.debtBalance)>0n&&(x.input.debtRate===null||x.input.debtDue===null))))fail('资金条件未完整确认');if(x.status==='confirmed'){if(BigInt(x.allocations.cash)<BigInt(x.input.emergency)+BigInt(x.input.fixed)+BigInt(x.input.future))fail('现金分配不足已填写的必要支出与应急底线');if(BigInt(x.allocations.debt)>BigInt(x.input.debtBalance))fail('还款分配超过负债余额');}stamp(x.createdAt);if(x.receiptApplied!==null){exactKeys(x.receiptApplied,['snapshotId','cashItemId','at'],'工资入账');id(x.receiptApplied.snapshotId);id(x.receiptApplied.cashItemId);stamp(x.receiptApplied.at);if(x.status!=='confirmed')fail('未确认计划不能入账');}}
function validateReport(x){
  exactKeys(x,['format','version','id','source','asOf','generatedAt','strategy','symbols','signals','backtest','paperTrades','notes','importedAt','research'],'量化报告');
  if(x.format!=='goose-investment-report'||x.version!==1)fail('不支持的量化报告版本');id(x.id);str(x.source,'报告来源',300);str(x.strategy,'策略名称',200);if(!x.source.trim()||!x.strategy.trim())fail('报告必须标明来源和策略');date(x.asOf);if(x.asOf>localToday())fail('报告数据日期不能在未来');stamp(x.generatedAt);if(Date.parse(x.generatedAt)>Date.now()+300000)fail('报告生成时间在未来');stamp(x.importedAt);str(x.notes,'报告说明',10000);
  if(!Array.isArray(x.symbols)||!x.symbols.length||x.symbols.length>100||new Set(x.symbols).size!==x.symbols.length)fail('报告标的无效');x.symbols.forEach(symbol);
  if(!Array.isArray(x.signals)||x.signals.length>100)fail('信号无效');const seen=new Set();for(const s of x.signals){exactKeys(s,['symbol','stance','basis','risk','quality'],'信号');symbol(s.symbol);if(!x.symbols.includes(s.symbol)||seen.has(s.symbol)||!['hold','watch','accumulate','reduce'].includes(s.stance)||!['low','medium','high'].includes(s.quality))fail('信号标的、方向或证据质量无效');seen.add(s.symbol);str(s.basis,'信号依据',4000);str(s.risk,'风险',4000);if(!s.basis.trim()||!s.risk.trim())fail('信号须有依据和风险');}
  if(x.backtest!==null){const b=x.backtest;exactKeys(b,['from','to','returnBps','maxDrawdownBps','commissionBps','slippageBps','tradeCount','limitations'],'回测');date(b.from);date(b.to);if(b.from>b.to||b.to>x.asOf)fail('回测区间无效');for(const k of ['returnBps','maxDrawdownBps','commissionBps','slippageBps','tradeCount'])if(!Number.isSafeInteger(b[k])||Math.abs(b[k])>1000000000||(k!=='returnBps'&&b[k]<0))fail('回测指标无效');str(b.limitations,'回测局限',4000);}
  if(!Array.isArray(x.paperTrades)||x.paperTrades.length>10000)fail('模拟交易记录无效');for(const t of x.paperTrades){exactKeys(t,['date','symbol','side','quantity','price','totalCost'],'模拟成交');date(t.date);symbol(t.symbol);if(t.date>x.asOf||!x.symbols.includes(t.symbol)||!['buy','sell'].includes(t.side)||!Number.isSafeInteger(t.quantity)||t.quantity<=0)fail('模拟成交无效');amount(t.price);amount(t.totalCost);}
  if(Object.hasOwn(x,'research'))validateResearch(x.research,x);
}
export function validatePlanning(p){
  safe(p);exactKeys(p,['schemaVersion',...LISTS],'投资规划');if(p.schemaVersion!==1)fail('不支持的投资规划版本');
  for(const key of LISTS){if(!Array.isArray(p[key])||p[key].length>10000)fail('规划集合无效');const ids=new Set();for(const x of p[key]){obj(x,'规划记录');id(x.id);if(ids.has(x.id))fail('规划记录编号重复');ids.add(x.id);if(key==='snapshots')validateSnapshot(x);else if(key==='reports')validateReport(x);else if(key==='salaryPlans')validatePlan(x);else{exactKeys(x,['id','symbol','name','createdAt'],'关注标的');symbol(x.symbol);str(x.name,'标的名称',100);stamp(x.createdAt);}}}
  if(new Set(p.watchlist.map(x=>x.symbol)).size!==p.watchlist.length)fail('关注标的重复');
  if(new Set(p.reports.map(x=>canonical([x.source,x.asOf,x.strategy]))).size!==p.reports.length)fail('同来源日期策略的报告重复');
  const snapshotMap=new Map(p.snapshots.map(x=>[x.id,x])),done=new Set();
  for(const s of p.snapshots){if(done.has(s.id))continue;const path=new Set();let current=s;while(current&&!done.has(current.id)){if(path.has(current.id))fail('快照修订不能循环');path.add(current.id);if(current.replacesId===null)break;const prior=snapshotMap.get(current.replacesId);if(!prior)fail('快照修订引用不存在');if(prior.date>current.date)fail('快照修订日期不能倒退');current=prior;}for(const x of path)done.add(x);}
  const applied=new Set();
  for(const plan of p.salaryPlans)if(plan.receiptApplied){
    const a=plan.receiptApplied,snap=p.snapshots.find(s=>s.id===a.snapshotId);
    if(!snap||snap.voided||snap.date!==plan.date||applied.has(snap.id))fail('工资入账快照无效或重复');applied.add(snap.id);
    const prior=p.snapshots.find(s=>s.id===snap.replacesId);
    const target=snap.items.find(i=>i.id===a.cashItemId),old=prior?.items.find(i=>i.id===a.cashItemId);
    if(!prior||prior.date!==plan.date||snapshotTotals(prior).cash!==plan.input.cashBefore||!target||!old||!CASH.has(target.type)||target.balance===null||old.balance===null||BigInt(target.balance)!==BigInt(old.balance)+BigInt(plan.input.receipt))fail('工资入账与到账前现金不一致');
    const expected=copy(prior.items);expected.find(i=>i.id===a.cashItemId).balance=target.balance;
    if(canonical(expected)!==canonical(snap.items))fail('工资入账不能修改其他资产');
  }
  return p;
}
export function saveSnapshot(state,input){const next=withPlanning(state);const x={id:uid(),date:input.date,items:input.items.map(i=>({id:i.id||uid(),name:i.name,type:i.type,balance:i.balance,annualRate:i.annualRate??null,dueDate:i.dueDate??null,note:i.note||''})),note:input.note||'',createdAt:now(),voided:false,replacesId:input.replacesId??null};validateSnapshot(x);next.planning.snapshots.push(x);validatePlanning(next.planning);return next;}
export function voidSnapshot(state,snapshotId){const next=withPlanning(state),x=next.planning.snapshots.find(s=>s.id===snapshotId);if(!x)fail('快照不存在');if(next.planning.salaryPlans.some(p=>p.receiptApplied?.snapshotId===snapshotId))fail('此快照关联已确认工资入账，保留其记录');x.voided=true;return next;}
export function latestSnapshot(state,asOf=localToday()){const all=(state.planning?.snapshots||[]).filter(s=>!s.voided&&s.date<=asOf);const replaced=new Set(all.map(s=>s.replacesId).filter(Boolean));return all.slice().reverse().filter(s=>!replaced.has(s.id)).sort((a,b)=>b.date.localeCompare(a.date)||b.createdAt.localeCompare(a.createdAt)).at(0)||null;}
export function snapshotTotals(snapshot){if(!snapshot)return{assets:null,liabilities:null,netWorth:null,cash:null,unknownAssets:0,unknownLiabilities:0};validateSnapshot(snapshot);let a=0n,d=0n,c=0n,ua=0,ud=0,uc=0;for(const i of snapshot.items){if(DEBTS.has(i.type)){if(i.balance===null)ud++;else d+=BigInt(i.balance);}else{if(i.balance===null)ua++;else a+=BigInt(i.balance);if(CASH.has(i.type)){if(i.balance===null)uc++;else c+=BigInt(i.balance);}}}return{assets:ua?null:String(a),liabilities:ud?null:String(d),netWorth:ua||ud?null:String(a-d),cash:uc?null:String(c),unknownAssets:ua,unknownLiabilities:ud};}
export function addWatch(state,input){const next=withPlanning(state);symbol(input.symbol);if(next.planning.watchlist.some(x=>x.symbol===input.symbol))fail('已关注此标的');next.planning.watchlist.push({id:uid(),symbol:input.symbol,name:input.name||input.symbol,createdAt:now()});validatePlanning(next.planning);return next;}
export function removeWatch(state,watchId){const next=withPlanning(state);next.planning.watchlist=next.planning.watchlist.filter(x=>x.id!==watchId);return next;}
export function saveReport(state,input){const next=withPlanning(state),x={...copy(input),id:input.id||uid(),importedAt:now()};validateReport(x);const content=r=>canonical(Object.fromEntries(Object.entries(r).filter(([k])=>!['id','importedAt'].includes(k))));if(next.planning.reports.some(r=>r.id===x.id||content(r)===content(x)||(r.source===x.source&&r.asOf===x.asOf&&r.strategy===x.strategy)))fail('同一量化报告已导入');next.planning.reports.push(x);validatePlanning(next.planning);return next;}
export function reportAssessment(report,asOf=localToday()){
  date(asOf);const days=Math.floor((Date.parse(asOf)-Date.parse(report.asOf))/86400000);const warnings=[];let datasetStale=false;
  if(days>3)warnings.push(`行情已距今${days}个自然日，仅供历史复盘`);if(days<0)warnings.push('报告日期在当前日期之后，不能用于当日决策');
  warnings.push('导入内容为来源声明，未由本应用独立复算；不等于实时价格或当前交易建议');if(!report.backtest)warnings.push('缺少回测与交易成本结果');if(!report.signals.length)warnings.push('缺少策略信号');
  if(report.research){
    const research=report.research,dataset=research.dataset;
    warnings.push('本报告仅为历史研究，决策权限已禁用；信号不是当前买卖建议');
    const dataDays=Math.floor((Date.parse(asOf)-Date.parse(dataset.through))/86400000);datasetStale=dataDays>3||dataDays<0;
    if(dataDays>3)warnings.push(`研究数据实际截止${dataset.through}，距今${dataDays}个自然日；报告生成时间不能使历史数据变新`);
    if(dataset.quality==='synthetic')warnings.push('完全虚构数据：只验证研究流程，不能证明真实市场价格、收益或策略有效');
    if(dataset.quality==='blocked')warnings.push('数据健康检查已阻断：缺失或未通过校验，不能用来形成投资决策');
    if(!dataset.crossValidated)warnings.push('单一来源，未通过独立来源交叉核对；来源自评不是独立核验');
    if(!dataset.unitVerified)warnings.push('价格单位未核验，不能将数字当作真实人民币价格');
    if(research.reportKind==='pre1445-test')warnings.push(research.generatedBeforeCutoff?'14:45前生成流程测试；不执行交易，不代表可交易信号':'14:45前生成条件未通过；仅供流程排查');
  }else warnings.push('旧版报告未包含数据来源指纹、四策略对比及训练验证测试证据，保持未核实');
  return{stale:days>3||days<0||datasetStale,warnings};
}

// This is an editable budgeting rule, not a return forecast or security recommendation.
export function calculateAllocation(input){
  validateAllocationInput(input);const warnings=[];
  const missing=INPUT_MONEY.filter(k=>input[k]===null);
  if(missing.length)warnings.push(`资金条件有${missing.length}项未知：请补全，不能按零视为真实余额`);
  if(input.cashAsOf!==input.date)warnings.push('现金余额日期与计划日期不同，请重新确认到账前余额');
  if(input.date!==localToday())warnings.push('这是历史或未来计划，不能作为今天的可用资金');
  if(input.debtBalance!==null&&BigInt(input.debtBalance)>0n&&(input.debtRate===null||input.debtDue===null))warnings.push('待还负债的利率或还款日未知');
  if(input.debtBalance!==null&&BigInt(input.debtBalance)>0n&&input.debtDue!==null&&input.debtDue<input.date)warnings.push('负债还款日已过，请确认是否逾期和实际应付金额');
  const available=BigInt(input.receipt)+(input.cashBefore===null?0n:BigInt(input.cashBefore));
  const bounds={available:input.cashBefore===null?null:available.toString(),budgetCap:available.toString(),knownMinimum:available.toString()};
  const complete=!missing.length&&input.cashAsOf===input.date&&input.date===localToday()&&!(input.debtBalance!==null&&BigInt(input.debtBalance)>0n&&(input.debtRate===null||input.debtDue===null));
  amount(available.toString());
  const allocations={cash:available.toString(),debt:'0',investment:'0',savings:'0',free:'0'};
  if(!complete){warnings.push('暂将已知资金保留现金；不生成自动投资或还款金额');return{...bounds,total:available.toString(),allocations,warnings,canConfirm:false};}
  let left=available;
  const take=(key,want)=>{const n=want>left?left:want;allocations[key]=n.toString();left-=n;return n;};
  const reserve=BigInt(input.emergency)+BigInt(input.fixed)+BigInt(input.future);
  take('cash',reserve);
  const repayment=BigInt(input.repayment),debt=BigInt(input.debtBalance);
  if(repayment>debt)warnings.push('计划还款超过所填负债余额，已按余额封顶；未自动计算应计利息');
  take('debt',repayment>debt?debt:repayment);
  take('investment',BigInt(input.investment));take('savings',BigInt(input.savings));take('free',BigInt(input.free));
  if(left>0n)allocations.cash=(BigInt(allocations.cash)+left).toString();
  const wanted=reserve+(repayment>debt?debt:repayment)+BigInt(input.investment)+BigInt(input.savings)+BigInt(input.free);
  if(wanted>available)warnings.push('资金不足：按现金保障、还款、定投、储蓄、自由支配顺序封顶；请编辑确认');
  warnings.push('风险偏好仅作计划标签；未预测收益，未执行交易、还款或转账');
  return{...bounds,total:available.toString(),allocations,warnings,canConfirm:true};
}
export function saveSalaryPlan(state,input){const next=withPlanning(state);const proposal=calculateAllocation(input.input);const plan={id:input.id||uid(),date:input.date,input:copy(input.input),allocations:copy(input.allocations||proposal.allocations),status:input.status||'draft',createdAt:now(),receiptApplied:null};if(next.planning.salaryPlans.some(x=>x.id===plan.id))fail('计划已存在，请另存修订计划');validatePlan(plan);if(plan.status==='confirmed'){if(!proposal.canConfirm)fail('请确认当日完整资金条件');if(BigInt(plan.allocations.cash)<BigInt(plan.input.emergency)+BigInt(plan.input.fixed)+BigInt(plan.input.future))fail('现金分配不足已填写的必要支出与应急底线');if(BigInt(plan.allocations.debt)>BigInt(plan.input.debtBalance))fail('还款分配超过负债余额');}next.planning.salaryPlans.push(plan);validatePlanning(next.planning);return next;}
export function confirmSalaryReceipt(state,planId,{snapshotId,cashItemId}){
  const next=withPlanning(state),plan=next.planning.salaryPlans.find(p=>p.id===planId);if(!plan||plan.status!=='confirmed'||plan.receiptApplied)fail('计划未确认或工资已经入账');if(!calculateAllocation(plan.input).canConfirm)fail('请重新确认当日资金条件');const snap=latestSnapshot(next);if(!snap||snap.id!==snapshotId||snap.date!==plan.date||snapshotTotals(snap).cash!==plan.input.cashBefore)fail('快照不是当日已确认的到账前现金余额，请先更新快照');const target=snap.items.find(i=>i.id===cashItemId);if(!target||!CASH.has(target.type)||target.balance===null)fail('请选已知余额的银行或现金账户');
  const items=copy(snap.items);items.find(i=>i.id===cashItemId).balance=(BigInt(target.balance)+BigInt(plan.input.receipt)).toString();const result=saveSnapshot(next,{date:plan.date,items,note:'经用户确认收到工资/奖金；分配方案未执行',replacesId:snap.id});const newSnap=result.planning.snapshots.at(-1);result.planning.salaryPlans.find(p=>p.id===planId).receiptApplied={snapshotId:newSnap.id,cashItemId,at:now()};validatePlanning(result.planning);return result;
}
export function buildReviewPackage(state,asOf=localToday()){date(asOf);const snap=latestSnapshot(state,asOf);return{format:'goose-ai-review-input',version:1,generatedAt:now(),asOf,privacy:'仅本机导出，未发送给AI或第三方',facts:{snapshot:snap?copy(snap):null,totals:snapshotTotals(snap),historicalPositions:copy((state.imports||[]).filter(x=>x.asOf<=asOf)),reports:copy((state.planning?.reports||[]).filter(x=>x.asOf<=asOf)),watchlist:copy(state.planning?.watchlist||[]),salaryPlans:copy((state.planning?.salaryPlans||[]).filter(x=>x.date<=asOf))},modelInferences:[],actionSuggestions:[],constraints:['事实按来源及日期核验','模型推测不属于回测结论','未知行情/余额保持未知','不得自动交易或发送个人数据']};}

export function buildDailyDecision(state,asOf=localToday()) {
  date(asOf);
  const report=(state.planning?.reports||[]).filter(r=>r.asOf<=asOf).slice().sort((a,b)=>b.asOf.localeCompare(a.asOf)||b.generatedAt.localeCompare(a.generatedAt))[0]||null;
  const snap=latestSnapshot(state,asOf),assessment=report?reportAssessment(report,asOf):null;
  const warnings=assessment?[...assessment.warnings]:['缺少可用量化报告，不生成当天买卖结论'];
  if(!snap)warnings.push('尚无完整资产快照');else if(snap.date!==asOf)warnings.push('资产快照不是当日确认余额');
  if(!report||report.asOf!==asOf)warnings.push('没有当日来源报告；历史信号不能冒充今天的信号');
  const embeddedPaperTradeCount=report?.paperTrades.length||0;
  const paperTradeCount=report?.research?report.research.comparison.find(item=>item.strategy==='v1_ma20').tradeCount:embeddedPaperTradeCount;
  const paperTradeCountSource=report?.research?'research-simulation':'embedded-paper-records';
  return {format:'goose-daily-decision',version:1,date:asOf,generatedAt:now(),status:!report?'missing':report.research||report.asOf!==asOf||assessment.stale?'historical':'source-unverified',sourceReport:report?{id:report.id,source:report.source,asOf:report.asOf,strategy:report.strategy}:null,facts:{snapshotDate:snap?.date||null,netWorth:snapshotTotals(snap).netWorth,positionsAsOf:[...new Set((state.imports||[]).filter(x=>x.asOf<=asOf).map(x=>x.asOf))],signalCount:report?.signals.length||0,paperTradeCount,embeddedPaperTradeCount,paperTradeCountSource,backtestAvailable:!!report?.backtest},warnings,scenarios:[{name:'持有',condition:'先核验投资逻辑、现有仓位与风险承受能力'},{name:'观察',condition:'行情、成本或资金缺失时补充证据'},{name:'分批买入',condition:'仅在预算、风险及证据明确后另行研究，无自动指令'},{name:'分批减仓',condition:'先核验集中度、现金需求及成本，无自动指令'}],action:'先核实数据；本报告不执行交易'};
}

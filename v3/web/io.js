import {emptyState, validateState} from './domain.js';
import {emptyPlanning, validatePlanning} from './planning.js';

const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', {fatal: true});
export const MAX_BYTES = 8 * 1024 * 1024;
export const KDF_ITERATIONS = 600000;
let connection, revision = 0, revisionToken = 0, warning = '';
const fail = (message) => { throw new Error(message); };
const copy = (value) => JSON.parse(JSON.stringify(value));
const object = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const PLANNING_COLLECTIONS = ['snapshots', 'reports', 'watchlist', 'salaryPlans'];

export function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (object(value)) return '{' + Object.keys(value).filter(k => value[k] !== undefined).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  if (value === undefined || typeof value === 'function' || (typeof value === 'number' && !Number.isFinite(value))) fail('数据含无效值');
  return JSON.stringify(value);
}

// Reject duplicate keys rather than silently accepting JSON.parse's last value.
export function parseJsonStrict(text, maxBytes = MAX_BYTES) {
  if (typeof text !== 'string' || enc.encode(text).length > maxBytes) fail('文件过大或格式无效');
  let at = 0;
  const ws = () => { while (/[ \t\r\n]/.test(text[at] || '') && at < text.length) at++; };
  function string() {
    const begin = at++;
    while (at < text.length) {
      if (text[at] === '\\') { at += 2; continue; }
      if (text[at++] === '"') return JSON.parse(text.slice(begin, at));
    }
    fail('JSON 字符串未结束');
  }
  function value(depth) {
    if (depth > 50) fail('JSON 嵌套过深');
    ws(); const ch = text[at];
    if (ch === '"') return string();
    if (ch === '{') {
      at++; const result = {}, keys = new Set(); ws();
      if (text[at] === '}') { at++; return result; }
      while (at < text.length) {
        ws(); if (text[at] !== '"') fail('JSON 字段无效');
        const key = string();
        if (keys.has(key) || ['__proto__', 'prototype', 'constructor'].includes(key)) fail('JSON 含重复或不安全字段');
        keys.add(key); ws(); if (text[at++] !== ':') fail('JSON 分隔符无效');
        result[key] = value(depth + 1); ws();
        const next = text[at++]; if (next === '}') return result;
        if (next !== ',') fail('JSON 对象未结束');
      }
    } else if (ch === '[') {
      at++; const result = []; ws(); if (text[at] === ']') { at++; return result; }
      while (at < text.length) {
        result.push(value(depth + 1)); ws(); const next = text[at++];
        if (next === ']') return result;
        if (next !== ',') fail('JSON 数组未结束');
      }
    } else {
      const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(at));
      if (token) {
        at += token[0].length; const result = JSON.parse(token[0]);
        if (typeof result === 'number' && (!Number.isFinite(result) || Math.abs(result) > Number.MAX_SAFE_INTEGER)) fail('JSON 数字超出安全范围');
        return result;
      }
    }
    fail('JSON 格式无效');
  }
  const result = value(0); ws(); if (at !== text.length) fail('JSON 含多余内容');
  return result;
}

export function migrateState(input) {
  const hadPlanning = object(input) && Object.hasOwn(input, 'planning');
  const state = parseJsonStrict(JSON.stringify(input));
  if (!object(state) || !Number.isInteger(state.schemaVersion)) fail('缺少数据结构版本');
  if (hadPlanning && !Object.hasOwn(state, 'planning')) fail('规划数据无效，不能静默丢弃扩展');
  if (state.schemaVersion === 0) {
    state.imports ??= []; state.audit ??= []; state.schemaVersion = 1;
  }
  if (state.schemaVersion !== 1) fail('不支持此数据版本；请保留原备份并使用相应新版');
  if (!Object.hasOwn(state, 'planning')) state.planning = emptyPlanning();
  validatePlanning(state.planning);
  validateState(state); validateImports(state.imports);
  return state;
}

async function digest(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(canonical(value))))).map(b => b.toString(16).padStart(2, '0')).join('');
}

function database() {
  if (connection) return connection;
  connection = new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) { reject(new Error('此浏览器无法打开本地数据库')); return; }
    const req = indexedDB.open('goose-v3-private', 2);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains('snapshots')) req.result.createObjectStore('snapshots', {keyPath: 'id'});
    };
    req.onerror = () => reject(new Error('本地数据库打开失败；请保留并恢复备份'));
    req.onblocked = () => reject(new Error('请关闭其他养鹅计划窗口后重试升级'));
    req.onsuccess = () => {
      req.result.onversionchange = () => { req.result.close(); connection = undefined; warning = '数据库版本已变化，请重新打开应用'; };
      resolve(req.result);
    };
  });
  return connection;
}

async function readRecords() {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('snapshots', 'readonly');
    const req = tx.objectStore('snapshots').getAll();
    req.onsuccess = () => resolve(req.result);
    tx.onabort = tx.onerror = () => reject(new Error('读取本机数据失败'));
  });
}

async function validRecord(record) {
  if (!record || !Number.isSafeInteger(record.revision) || record.revision < 1) fail('本地记录版本无效');
  const state = migrateState(record.payload);
  if (await digest(record.payload) !== record.checksum) fail('本地记录校验失败');
  return state;
}

export async function loadState() {
  const rows = await readRecords();
  const current = rows.find(r => r.id === 'current');
  if (!current) {
    if (rows.length) fail('本地数据不完整，请从加密备份恢复');
    revision = revisionToken = 0; return emptyState();
  }
  revisionToken = current.revision ?? 0;
  revision = Number.isSafeInteger(current.revision) && current.revision > 0 ? current.revision : 0;
  try { return await validRecord(current); }
  catch {
    const recovery = rows.find(r => r.id === 'previous');
    try {
      const state = await validRecord(recovery); revision = Math.max(revision, recovery.revision);
      warning = '当前记录校验失败，已读取上一份完整副本；最近一次改动可能缺失。请先导出备份并检查流水。';
      return state;
    } catch { fail('本地数据与恢复副本均校验失败；请勿继续录入，请从加密备份恢复'); }
  }
}

export async function persistState(input) {
  const state = migrateState(input);
  const checksum = await digest(state);
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('snapshots', 'readwrite');
    const store = tx.objectStore('snapshots'); const req = store.get('current');
    let issue = '';
    req.onsuccess = () => {
      const old = req.result;
      if ((old?.revision ?? 0) !== revisionToken) { issue = '另一窗口已更新数据，请刷新后再记录'; tx.abort(); return; }
      if (old && !warning) store.put({...old, id: 'previous'});
      store.put({id: 'current', revision: revision + 1, checksum, payload: state});
    };
    tx.oncomplete = () => { revision++; revisionToken = revision; warning = ''; resolve(); };
    tx.onerror = tx.onabort = () => reject(new Error(issue || '保存失败：本机存储不可用或空间不足。此笔未保存，请导出已有数据。'));
  });
}

const b64 = bytes => {
  let text = ''; for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text);
};
function unb64(text, max) {
  if (typeof text !== 'string' || text.length > max * 1.4 + 8 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) fail('加密备份编码无效');
  let raw; try { raw = atob(text); } catch { fail('加密备份编码无效'); }
  if (raw.length > max || btoa(raw) !== text) fail('加密备份编码无效');
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}
async function derive(password, salt, iterations) {
  if (typeof password !== 'string' || password.length < 12 || password.length > 1024) fail('备份密码至少 12 个字符；请单独妥善保存，无法找回');
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name: 'PBKDF2', salt, iterations, hash: 'SHA-256'}, key, {name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']);
}

export async function encryptBackup(input, password) {
  const state = migrateState(input);
  const header = {format: 'goose-v3-encrypted', version: 2, cipher: 'AES-GCM-256', kdf: 'PBKDF2-SHA256', iterations: KDF_ITERATIONS,
    salt: b64(crypto.getRandomValues(new Uint8Array(16))), iv: b64(crypto.getRandomValues(new Uint8Array(12))), createdAt: new Date().toISOString()};
  const key = await derive(password, unb64(header.salt, 16), header.iterations);
  const ciphertext = await crypto.subtle.encrypt({name: 'AES-GCM', iv: unb64(header.iv, 12), additionalData: enc.encode(canonical(header))}, key, enc.encode(canonical(state)));
  return {...header, ciphertext: b64(new Uint8Array(ciphertext))};
}

export async function decryptBackup(input, password) {
  const doc = typeof input === 'string' ? parseJsonStrict(input, MAX_BYTES * 1.5) : parseJsonStrict(JSON.stringify(input), MAX_BYTES * 1.5);
  const keys = ['format','version','cipher','kdf','iterations','salt','iv','createdAt','ciphertext'];
  if (!object(doc) || Object.keys(doc).length !== keys.length || keys.some(k => !Object.hasOwn(doc, k)) || doc.format !== 'goose-v3-encrypted' || ![1, 2].includes(doc.version) || doc.cipher !== 'AES-GCM-256' || doc.kdf !== 'PBKDF2-SHA256' || !Number.isInteger(doc.iterations) || doc.iterations < 100000 || doc.iterations > 2000000 || typeof doc.createdAt !== 'string' || !Number.isFinite(Date.parse(doc.createdAt))) fail('不支持或无效的加密备份');
  const salt = unb64(doc.salt, 16), iv = unb64(doc.iv, 12), bytes = unb64(doc.ciphertext, MAX_BYTES + 16);
  if (salt.length !== 16 || iv.length !== 12 || bytes.length < 16) fail('加密备份不完整');
  const {ciphertext, ...header} = doc;
  const key = await derive(password, salt, doc.iterations);
  let plain;
  try { plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv, additionalData: enc.encode(canonical(header))}, key, bytes); }
  catch { fail('密码错误或备份已损坏；现有数据未改动'); }
  return migrateState(parseJsonStrict(dec.decode(plain)));
}

const fields = (x, names) => object(x) && Object.keys(x).length === names.length && names.every(k => Object.hasOwn(x, k));
const short = (s, max = 128) => typeof s === 'string' && s.length > 0 && s.length <= max && !/[\u0000-\u001f]/.test(s);
const dateOK = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)) && new Date(s).toISOString().slice(0,10) === s && s <= new Date().toLocaleDateString('en-CA');
const decimal = x => x === null || typeof x === 'string' && /^-?(?:0|[1-9]\d{0,15})(?:\.\d{1,12})?$/.test(x);

function parseP1Document(doc) {
  if (!fields(doc, ['schema_version','as_of','accounts','transaction_history_complete']) || doc.schema_version !== 1 || !dateOK(doc.as_of) || doc.transaction_history_complete !== false || !Array.isArray(doc.accounts) || !doc.accounts.length || doc.accounts.length > 1000) fail('不是受支持的 P1 历史持仓快照');
  const accounts = [], positions = [], seen = new Set();
  for (const a of doc.accounts) {
    if (!fields(a, ['account','broker','coverage','cash','positions']) || !/^[A-Za-z0-9_-]{1,32}$/.test(a.account) || seen.has(a.account) || !short(a.broker) || !['partial','complete'].includes(a.coverage) || !decimal(a.cash) || a.cash !== null && a.cash.startsWith('-') || a.coverage === 'complete' && a.cash === null || !Array.isArray(a.positions) || a.positions.length > 10000) fail('P1 账户字段或未知余额标记无效');
    seen.add(a.account); const symbols = new Set();
    accounts.push({account: a.account, broker: a.broker, coverage: a.coverage, cash: a.cash});
    for (const p of a.positions) {
      if (!fields(p, ['symbol','kind','quantity','available','broker_cost','cost_basis']) || !/^\d{6}\.(SH|SZ)$/.test(p.symbol) || symbols.has(p.symbol) || !['stock','etf'].includes(p.kind) || !Number.isSafeInteger(p.quantity) || p.quantity <= 0 || !Number.isSafeInteger(p.available) || p.available < 0 || p.available > p.quantity || !decimal(p.broker_cost) || p.cost_basis !== 'broker_display') fail('P1 持仓字段无效');
      symbols.add(p.symbol); positions.push({account: a.account, ...copy(p)});
    }
  }
  return {accounts, positions};
}
export async function parseP1Import(text) {
  const doc = parseJsonStrict(text);
  const {accounts, positions} = parseP1Document(doc);
  return {id: crypto.randomUUID(), source: 'P1 f6c117f 历史快照（手动导入）', asOf: doc.as_of, fingerprint: await digest(doc), accounts, positions, importedAt: new Date().toISOString(), valuationIncluded: false};
}

function validateImports(items) {
  if (!Array.isArray(items) || items.length > 10000) fail('导入记录无效');
  const seen = new Set();
  for (const x of items) {
    if (!fields(x,['id','source','asOf','fingerprint','accounts','positions','importedAt','valuationIncluded']) || !short(x.id) || !short(x.source,256) || !dateOK(x.asOf) || !/^[a-f0-9]{64}$/.test(x.fingerprint) || seen.has(x.fingerprint) || x.valuationIncluded !== false || !Array.isArray(x.accounts) || !Array.isArray(x.positions) || typeof x.importedAt !== 'string' || !Number.isFinite(Date.parse(x.importedAt))) fail('导入来源或去重记录无效');
    seen.add(x.fingerprint);
    const ids = new Set(x.accounts.map(a=>a.account));
    for (const a of x.accounts) if (!fields(a,['account','broker','coverage','cash'])) fail('备份中的 P1 账户无效');
    for (const p of x.positions) if (!fields(p,['account','symbol','kind','quantity','available','broker_cost','cost_basis']) || !ids.has(p.account)) fail('备份中的 P1 持仓无效');
    parseP1Document({schema_version:1,as_of:x.asOf,transaction_history_complete:false,accounts:x.accounts.map(a=>({...a,positions:x.positions.filter(p=>p.account===a.account).map(({account,...p})=>p)}))});
  }
}

export function applyP1Import(input, preview) {
  const state = migrateState(input);
  validateImports([preview]);
  if (state.imports.some(x => x.fingerprint === preview.fingerprint)) fail('同一快照已导入，不会重复添加');
  state.imports.push(copy(preview));
  state.audit.push({id: crypto.randomUUID(), action: 'import-p1', entityId: preview.id, at: new Date().toISOString()});
  return migrateState(state);
}

export function previewRestore(currentInput, incomingInput) {
  const current = migrateState(currentInput), incoming = migrateState(incomingInput);
  const conflicts = [];
  for (const key of ['accounts','entries','imports','audit']) {
    const map = new Map(current[key].map(x => [x.id, x]));
    for (const item of incoming[key]) if (map.has(item.id) && canonical(map.get(item.id)) !== canonical(item)) conflicts.push({type: key, id: item.id});
  }
  for (const key of PLANNING_COLLECTIONS) {
    const map = new Map(current.planning[key].map(x => [x.id, x]));
    for (const item of incoming.planning[key]) if (map.has(item.id) && canonical(map.get(item.id)) !== canonical(item)) conflicts.push({type: `planning.${key}`, id: item.id});
  }
  const watchedSymbols = new Map(current.planning.watchlist.map(item => [item.symbol, item.id]));
  for (const item of incoming.planning.watchlist) {
    const existingId = watchedSymbols.get(item.symbol);
    if (existingId !== undefined && existingId !== item.id) conflicts.push({type: 'planning.watchlist-symbol', id: item.id});
  }
  const reportSource = item => canonical([item.source, item.asOf, item.strategy]);
  const reportSources = new Map(current.planning.reports.map(item => [reportSource(item), item.id]));
  for (const item of incoming.planning.reports) {
    const existingId = reportSources.get(reportSource(item));
    if (existingId !== undefined && existingId !== item.id) conflicts.push({type: 'planning.report-source', id: item.id});
  }
  for (const item of incoming.imports) {
    const same = current.imports.find(x=>x.fingerprint===item.fingerprint);
    const content = x=>canonical({asOf:x.asOf,accounts:x.accounts,positions:x.positions,valuationIncluded:x.valuationIncluded});
    if (same && content(same)!==content(item)) conflicts.push({type:'import-fingerprint',id:item.id});
  }
  return {accounts: incoming.accounts.length, entries: incoming.entries.length, imports: incoming.imports.length,
    ...Object.fromEntries(PLANNING_COLLECTIONS.map(key => [key, incoming.planning[key].length])), conflicts};
}
export function restoreMerge(currentInput, incomingInput) {
  const current = migrateState(currentInput), incoming = migrateState(incomingInput);
  if (previewRestore(current,incoming).conflicts.length) fail('备份存在同 ID 不同内容的冲突；请保留双方备份，不能自动合并');
  const importAliases = new Map(incoming.imports.map(x=>[x.id,current.imports.find(c=>c.fingerprint===x.fingerprint)?.id || x.id]));
  for (const a of incoming.audit) if(a.action==='import-p1' && importAliases.get(a.entityId)!==a.entityId){a.originalEntityId=a.entityId;a.entityId=importAliases.get(a.entityId);}
  for (const key of ['accounts','entries','imports','audit']) {
    const ids = new Set(current[key].map(x => x.id));
    const fingerprints = key === 'imports' ? new Set(current.imports.map(x => x.fingerprint)) : null;
    current[key].push(...incoming[key].filter(x => !ids.has(x.id) && (!fingerprints || !fingerprints.has(x.fingerprint))));
  }
  for (const key of PLANNING_COLLECTIONS) {
    const ids = new Set(current.planning[key].map(x => x.id));
    current.planning[key].push(...incoming.planning[key].filter(x => !ids.has(x.id)));
  }
  return migrateState(current);
}
export function restoreReplace(currentInput, incomingInput) {
  migrateState(currentInput); return migrateState(incomingInput);
}
export async function storageStatus() {
  const [persisted, estimate] = await Promise.all([navigator.storage?.persisted?.().catch(() => false), navigator.storage?.estimate?.().catch(() => ({}))]);
  return {persisted: !!persisted, usage: estimate?.usage || 0, quota: estimate?.quota || 0, warning};
}
export async function requestPersistence() { return !!(await navigator.storage?.persist?.().catch(() => false)); }

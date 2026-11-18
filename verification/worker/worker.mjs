// Private read-only snapshot host. No local proxy, broker, analytics or logging.
import {collectPulse} from './pulse.mjs';
import {research} from './holding-research.mjs';
import {scanBatch,scanView} from './market-batches.mjs';
import {importSignals} from './signal-archive.mjs';
import {browserApi,runnerApi} from './v4-api.mjs';
const MAX_BYTES = 1536 * 1024;
const SESSION_SECONDS = 7 * 86400;
const encoder = new TextEncoder();
const cookieName = '__Host-goose_session';
const assetPaths = new Set(['/investment/', '/investment/app.js', '/investment/styles.css',
  '/investment/model.mjs', '/investment/presentation.js', '/investment/cockpit.js', '/investment/radar-ui.js','/investment/decision-ui.js','/investment/boot.js','/investment/pwa.js']);
const LOGIN = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>养鹅 · 私人报告登录</title><link rel="stylesheet" href="/login.css"></head><body><main><h1>养鹅 · 私人报告</h1><p>输入专用登录口令。此入口只读，不连接券商。</p><form method="post" action="/login"><input type="hidden" name="csrf" value="__CSRF__"><label>私人登录口令<input type="password" name="code" autocomplete="current-password" minlength="43" maxlength="43" required></label><button type="submit">验证并查看报告</button></form><p>口令与 Windows 上传密钥相互独立，请勿转发。登录有效期7天。</p></main></body></html>`;
const DEVICE_ENTRY = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="goose-device-csrf" content="__CSRF__"><title>正在打开养鹅投资助手</title><link rel="stylesheet" href="/login.css"><script src="/enter.js" defer></script></head><body><main><h1>养鹅投资助手</h1><p id="entry-status" role="status">正在安全打开你的养鹅，无需输入口令……</p><noscript>当前浏览器未启用 JavaScript，无法完成安全登录。</noscript></main></body></html>`;
const DEVICE_ENTRY_JS = `(function(){var token=location.hash.slice(1);history.replaceState(null,'','/enter');var el=document.getElementById('entry-status');if(!/^[A-Za-z0-9_-]{43}$/.test(token)){el.textContent='这次打开链接不完整或已失效，请从已授权的设备重新打开。';return;}var c=new AbortController(),timer=setTimeout(function(){c.abort();},15000);fetch('/api/device-login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token,csrf:document.querySelector('meta[name="goose-device-csrf"]').content}),signal:c.signal}).then(function(r){if(!r.ok)throw new Error(r.status===401?'EXPIRED':'FAILED');location.replace('/investment/');}).catch(function(e){el.textContent=e.message==='EXPIRED'?'这次打开链接已过期或已使用，原口令与持仓数据未改变。':'暂时无法完成登录，请稍后重试。没有更改持仓或报告。';}).finally(function(){clearTimeout(timer);token='';});}());`;
const SECURITY = {
  'X-Goose-Release': 'entry-stability-20261113-1',
  'Cache-Control': 'no-store, private', 'Pragma': 'no-cache', 'Vary': 'Cookie',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
  'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
  'Strict-Transport-Security': 'max-age=31536000',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'
};
function response(body, status = 200, type = 'application/json; charset=utf-8', extra = {}) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body),
    {status, headers: {...SECURITY, 'Content-Type': type, ...extra}});
}
function serviceFailure(request,code) {
  const path=new URL(request.url).pathname;
  if(request.method==='GET'&&['/','/investment/','/login','/enter'].includes(path))return response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>养鹅 · 服务暂不可用</title><h1>养鹅投资助手</h1><p>原报告服务暂时无法读取，请稍后重试。持仓和历史数据未被修改。</p><p>错误代码：${code}</p><a href="/investment/">重新打开养鹅</a></html>`,503,'text/html; charset=utf-8');
  return response({error:code},503);
}
function hex(bytes) { return [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join(''); }
function equal(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let difference = 0; for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}
function base64url(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}
function decode64(value) {
  return Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0));
}
async function digest(value) { return hex(await crypto.subtle.digest('SHA-256', value)); }
async function hmac(secret, value) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return base64url(await crypto.subtle.sign('HMAC', key, encoder.encode(value)));
}
function ready(env) {
  return /^[a-f0-9]{64}$/.test(env.VIEW_KEY_SHA256 || '') &&
    /^[A-Za-z0-9_-]{43}$/.test(env.SESSION_SECRET || '') &&
    /^[A-Za-z0-9_-]{43}$/.test(env.UPLOAD_SECRET || '') && !!env.REPORT_DB && !!env.ASSETS;
}
function cookieValue(request,name) {
  const pair=(request.headers.get('Cookie')||'').split(';').map(s=>s.trim()).find(s=>s.startsWith(name+'='));
  return pair?.slice(name.length+1)||'';
}
async function loginChallenge(env,url,now) {
  const nonce=base64url(crypto.getRandomValues(new Uint8Array(32))),stamp=String(now);
  const token=nonce+'.'+stamp+'.'+await hmac(env.SESSION_SECRET,`login\n${url.origin}\n${stamp}\n${nonce}`);
  return {token,cookie:`__Host-goose_login=${nonce}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=600`};
}
async function validChallenge(request,env,url,token,now) {
  const [nonce,stamp,signature,...rest]=token.split('.');
  return !rest.length&&/^[A-Za-z0-9_-]{43}$/.test(nonce||'')&&/^\d{10}$/.test(stamp||'')&&
    now-Number(stamp)>=-30&&now-Number(stamp)<=600&&equal(nonce,cookieValue(request,'__Host-goose_login'))&&
    equal(signature,await hmac(env.SESSION_SECRET,`login\n${url.origin}\n${stamp}\n${nonce}`));
}
async function logoutToken(request,env,url) {
  return hmac(env.SESSION_SECRET,'logout\n'+url.origin+'\n'+cookieValue(request,cookieName));
}
async function sessionCookie(env,url,now) {
  const body=base64url(encoder.encode(JSON.stringify({v:1,iat:now,exp:now+SESSION_SECONDS,aud:url.origin})));
  return `${cookieName}=${body}.${await hmac(env.SESSION_SECRET,body)}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${SESSION_SECONDS}`;
}
async function deviceLink(request,env,url,now) {
  const stamp=request.headers.get('X-Goose-Time')||'',sha=request.headers.get('X-Goose-SHA256')||'';
  if(!/^\d{10}$/.test(stamp)||Math.abs(Number(stamp)-now)>300||!/^[a-f0-9]{64}$/.test(sha)||
    !equal(request.headers.get('Authorization'),'Goose-HMAC '+await hmac(env.UPLOAD_SECRET,`POST\n/api/device-link\n${stamp}\n${sha}`)))return response({error:'DEVICE_ISSUER_AUTH_REQUIRED'},401);
  let raw;try{raw=await limitedBody(request,32);}catch{return response({error:'DEVICE_REQUEST_REJECTED'},400);}
  if(!equal(await digest(raw),sha)||new TextDecoder().decode(raw)!=='{}')return response({error:'DEVICE_REQUEST_REJECTED'},400);
  const token=base64url(crypto.getRandomValues(new Uint8Array(32))),expires=now+600;
  // Store only the digest. The one-use token stays in the URL fragment, never a query string.
  await env.REPORT_DB.prepare('INSERT INTO cloud_device_links (token_hash,origin,created_epoch,expires_epoch,used_epoch) VALUES (?,?,?,?,NULL)')
    .bind(await digest(encoder.encode(token)),url.origin,now,expires).run();
  return response({path:'/enter#'+token,expiresAt:new Date(expires*1000).toISOString()});
}
async function deviceLogin(request,env,url,now) {
  if(request.headers.get('Origin')!==url.origin||request.headers.get('Content-Type')!=='application/json')return response({error:'DEVICE_ORIGIN_REJECTED'},403);
  let value;try{value=JSON.parse(new TextDecoder().decode(await limitedBody(request,512)));}catch{return response({error:'DEVICE_REQUEST_REJECTED'},400);}
  if(!/^[A-Za-z0-9_-]{43}$/.test(value?.token||'')||typeof value?.csrf!=='string'||!await validChallenge(request,env,url,value.csrf,now))return response({error:'DEVICE_CHALLENGE_REJECTED'},403);
  // One atomic consume prevents simultaneous or replayed requests from issuing a second session.
  const row=await env.REPORT_DB.prepare('UPDATE cloud_device_links SET used_epoch=? WHERE token_hash=? AND origin=? AND used_epoch IS NULL AND expires_epoch>? RETURNING token_hash')
    .bind(now,await digest(encoder.encode(value.token)),url.origin,now).first();
  if(!row)return response({error:'DEVICE_LINK_EXPIRED_OR_USED'},401);
  return response({status:'signed-in'},200,'application/json; charset=utf-8',{'Set-Cookie':await sessionCookie(env,url,now)});
}
async function session(request, env, url, now) {
  const cookie = (request.headers.get('Cookie') || '').split(';').map(s => s.trim()).find(s => s.startsWith(cookieName + '='));
  if (!cookie || cookie.length > 1024) return false;
  const token = cookie.slice(cookieName.length + 1), parts = token.split('.');
  if (parts.length !== 2 || !equal(await hmac(env.SESSION_SECRET, parts[0]), parts[1])) return false;
  try {
    const value = JSON.parse(new TextDecoder().decode(decode64(parts[0])));
    return value.v === 1 && value.aud === url.origin && Number.isInteger(value.exp) && Number.isInteger(value.iat) &&
      value.iat <= now + 30 && value.exp > now && value.exp - value.iat === SESSION_SECONDS;
  } catch { return false; }
}
async function limitedBody(request, max) {
  const declared = request.headers.get('Content-Length');
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > max)) throw new Error('BODY_LIMIT');
  const reader = request.body?.getReader(); if (!reader) throw new Error('BODY_REQUIRED');
  const chunks = []; let length = 0;
  while (true) {
    const {done, value} = await reader.read(); if (done) break;
    length += value.length; if (length > max) { await reader.cancel(); throw new Error('BODY_LIMIT'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}
async function upload(request, env, url, now) {
  if (url.search || request.headers.get('Content-Type') !== 'application/json') return response({error:'UPLOAD_REJECTED'}, 400);
  const stamp = request.headers.get('X-Goose-Time') || '', sha = request.headers.get('X-Goose-SHA256') || '';
  const signature = request.headers.get('Authorization') || '';
  if (!/^\d{10}$/.test(stamp) || Math.abs(Number(stamp) - now) > 300 || !/^[a-f0-9]{64}$/.test(sha) ||
      !equal(signature, 'Goose-HMAC ' + await hmac(env.UPLOAD_SECRET, `POST\n${url.pathname}\n${stamp}\n${sha}`)))
    return response({error:'UPLOAD_AUTH_REQUIRED'}, 401);
  let raw; try { raw = await limitedBody(request, MAX_BYTES); } catch { return response({error:'BODY_LIMIT'}, 413); }
  if (!equal(await digest(raw), sha)) return response({error:'UPLOAD_DIGEST_MISMATCH'}, 400);
  let value; try { value = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(raw)); } catch { return response({error:'INVALID_SNAPSHOT'}, 400); }
  const r = value?.report;
  if (value.format !== 'goose-private-cloud-snapshot' || value.version !== 1 ||
      !r || r.format !== 'goose-live-research' || r.version !== 1 || r.synthetic || r.testData ||
      typeof r.id !== 'string' || !/^[a-f0-9-]{36}$/.test(r.id) ||
      typeof r.generatedAt !== 'string' || !Number.isFinite(Date.parse(r.generatedAt)) ||
      Date.parse(r.generatedAt) > (now + 300) * 1000 ||
      !/^[a-f0-9]{64}$/.test(value.sourceArchiveSha256 || '') ||
      !Array.isArray(r.portfolio?.positions) || !Array.isArray(r.researchStates?.items) || r.researchStates.items.length !== 16 ||
      Object.keys(value).some(k => !['format','version','sourceArchiveSha256','exportedAt','report','runtime'].includes(k)))
    return response({error:'INVALID_SNAPSHOT'}, 400);
  const generated = new Date(r.generatedAt).toISOString(), received = new Date(now * 1000).toISOString();
  const stored = `{"syncedAt":${JSON.stringify(received)},"snapshot":${new TextDecoder().decode(raw)}}`;
  // A single bounded row; conditional update is atomic, so delayed uploads cannot replace newer reports.
  const result = await env.REPORT_DB.prepare(`INSERT INTO private_snapshot
    (id,report_id,generated_at,archive_sha256,content_sha256,upload_epoch,received_at,body)
    VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      report_id=excluded.report_id, generated_at=excluded.generated_at,
      archive_sha256=excluded.archive_sha256, content_sha256=excluded.content_sha256,
      upload_epoch=excluded.upload_epoch, received_at=excluded.received_at, body=excluded.body
    WHERE excluded.generated_at > private_snapshot.generated_at OR
      (excluded.generated_at = private_snapshot.generated_at AND excluded.report_id = private_snapshot.report_id
       AND excluded.archive_sha256 = private_snapshot.archive_sha256
       AND excluded.upload_epoch > private_snapshot.upload_epoch AND excluded.content_sha256 != private_snapshot.content_sha256)`)
    .bind(r.id, generated, value.sourceArchiveSha256, sha, now, received, stored).run();
  if (!result.meta.changes) {
    const old = await env.REPORT_DB.prepare('SELECT content_sha256, received_at FROM private_snapshot WHERE id=1').first();
    if (!old || old.content_sha256 !== sha) return response({error:'OLDER_OR_CONFLICTING_SNAPSHOT'}, 409);
    return response({status:'already-stored',sha256:sha,syncedAt:old.received_at});
  }
  return response({status:'stored',sha256:sha,syncedAt:received});
}
export async function handle(request, env, now = Math.floor(Date.now() / 1000)) {
  const url = new URL(request.url);
  if (url.protocol !== 'https:' && !['127.0.0.1','localhost'].includes(url.hostname)) return response({error:'HTTPS_REQUIRED'}, 400);
  // Some share clients append campaign tags. Canonicalize navigation only;
  // authentication and all API query restrictions remain unchanged.
  if(url.search&&request.method==='GET'&&['/','/investment/','/login','/enter'].includes(url.pathname)&&
    [...url.searchParams.keys()].every(k=>['utm_source','utm_medium','utm_campaign','utm_content','utm_term'].includes(k)))return response('',303,'text/plain',{Location:url.pathname});
  if ((url.search && !url.pathname.startsWith('/investment/api/v4/')) || /%|\\|\/{2}/.test(url.pathname)) return response({error:'NOT_FOUND'}, 404);
  if(url.pathname.startsWith('/api/runner/')) return runnerApi(request,env,now);
  if (!ready(env)) return serviceFailure(request,'PRIVATE_SERVICE_NOT_CONFIGURED');
  if(url.pathname==='/connection-check'&&request.method==='GET')return new Response(null,{status:204,headers:{...SECURITY,'X-Goose-Reachability':'1'}});
  if(url.pathname==='/api/device-link'&&request.method==='POST')return deviceLink(request,env,url,now);
  if(url.pathname==='/api/device-login'&&request.method==='POST')return deviceLogin(request,env,url,now);
  if(url.pathname==='/enter'&&request.method==='GET'){
    if(await session(request,env,url,now))return response('',303,'text/plain',{Location:'/investment/'});
    const challenge=await loginChallenge(env,url,now);
    return response(DEVICE_ENTRY.replace('__CSRF__',challenge.token),200,'text/html; charset=utf-8',{'Set-Cookie':challenge.cookie});
  }
  if(url.pathname==='/enter.js'&&request.method==='GET')return response(DEVICE_ENTRY_JS,200,'text/javascript; charset=utf-8');
  if(request.method==='GET'&&['/manifest.webmanifest','/sw.js','/offline','/offline.html','/icon-192.png','/icon-512.png','/recovery.js'].includes(url.pathname)){
    const asset=await env.ASSETS.fetch(request);return new Response(asset.body,{status:asset.status,headers:{...Object.fromEntries(asset.headers),...SECURITY,...(url.pathname==='/sw.js'?{'Service-Worker-Allowed':'/'}:{})}});
  }
  if (url.pathname === '/login' && request.method === 'GET') {
    const challenge=await loginChallenge(env,url,now);
    const html=LOGIN.replace('href="/login.css">','href="/login.css" media="print" data-goose-style><script src="/recovery.js" defer></script>');
    return response(html.replace('__CSRF__',challenge.token),200,'text/html; charset=utf-8',{'Set-Cookie':challenge.cookie});
  }
  if (url.pathname === '/login.css' && request.method === 'GET') {
    const asset = await env.ASSETS.fetch(new Request(url.origin + '/investment/styles.css'));
    return new Response(asset.body, {status:asset.status,headers:{...SECURITY,'Content-Type':'text/css; charset=utf-8'}});
  }
  if (url.pathname === '/api/pair' && request.method === 'GET') {
    const stamp=request.headers.get('X-Goose-Time')||'',nonce=request.headers.get('X-Goose-Challenge')||'';
    if(!/^\d{10}$/.test(stamp)||Math.abs(Number(stamp)-now)>300||!/^[A-Za-z0-9_-]{43}$/.test(nonce)||
      !equal(request.headers.get('Authorization'),'Goose-HMAC '+await hmac(env.UPLOAD_SECRET,`PAIR\n/api/pair\n${stamp}\n${nonce}`)))return response({error:'PAIR_AUTH_REQUIRED'},401);
    return response({status:'paired',nonce,proof:await hmac(env.UPLOAD_SECRET,'PAIRED\n'+nonce)});
  }
  if (url.pathname === '/login' && request.method === 'POST') {
    if (request.headers.get('Content-Type')?.split(';')[0] !== 'application/x-www-form-urlencoded') return response({error:'FORM_REQUIRED'},415);
    let raw; try { raw = await limitedBody(request,512); } catch { return response({error:'LOGIN_REJECTED'}, 400); }
    const form = new URLSearchParams(new TextDecoder().decode(raw)), code = form.get('code') || '';
    // Signed, cookie-bound CSRF also works when a browser transport rewrites
    // Origin. A foreign page cannot read the nonce or set a __Host- cookie.
    if (form.getAll('csrf').length!==1||!await validChallenge(request,env,url,form.get('csrf')||'',now))return response({error:'LOGIN_CSRF_REJECTED'},403);
    if (form.getAll('code').length !== 1 || !/^[A-Za-z0-9_-]{43}$/.test(code) || !equal(await digest(encoder.encode(code)),env.VIEW_KEY_SHA256))
      return response({error:'LOGIN_REJECTED'},401);
    return response('',303,'text/plain',{Location:'/investment/','Set-Cookie':await sessionCookie(env,url,now)});
  }
  if (url.pathname === '/api/upload' && request.method === 'POST') return upload(request,env,url,now);
  if(url.pathname==='/api/signals-import'&&request.method==='POST'){
    const stamp=request.headers.get('X-Goose-Time')||'',sha=request.headers.get('X-Goose-SHA256')||'';
    if(!/^\d{10}$/.test(stamp)||Math.abs(Number(stamp)-now)>300||!equal(request.headers.get('Authorization'),'Goose-HMAC '+await hmac(env.UPLOAD_SECRET,`POST\n/api/signals-import\n${stamp}\n${sha}`)))return response({error:'IMPORT_AUTH_REQUIRED'},401);
    const raw=await limitedBody(request,256*1024);if(!equal(await digest(raw),sha))return response({error:'IMPORT_DIGEST_REJECTED'},400);
    return response(await importSignals(env,JSON.parse(new TextDecoder().decode(raw))));
  }
  if (!await session(request,env,url,now)) {
    if (request.method === 'GET' && (url.pathname === '/' || url.pathname === '/investment/')) return response('',303,'text/plain',{Location:'/login'});
    return response({error:'PRIVATE_LOGIN_REQUIRED'},401);
  }
  if(url.pathname.startsWith('/investment/api/v4/'))return browserApi(request,env,now,await logoutToken(request,env,url));
  if (url.pathname === '/logout' && request.method === 'POST') {
    let form;try{form=new URLSearchParams(new TextDecoder().decode(await limitedBody(request,256)));}catch{return response({error:'LOGOUT_REJECTED'},403);}
    if(form.getAll('csrf').length!==1||!equal(form.get('csrf'),await logoutToken(request,env,url)))return response({error:'LOGOUT_REJECTED'},403);
    return response('',303,'text/plain',{Location:'/login','Set-Cookie':`${cookieName}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0`});
  }
  if(url.pathname==='/investment/api/refresh'&&request.method==='POST'){
    if(!equal(request.headers.get('X-Goose-CSRF'),await logoutToken(request,env,url)))return response({error:'REFRESH_CSRF_REJECTED'},403);
    return response(await collectPulse(env,'authenticated-open'));
  }
  if(url.pathname==='/investment/api/pulse'&&request.method==='GET'){
    const latest=await env.REPORT_DB.prepare('SELECT id,status,started_at,finished_at FROM cloud_pulse ORDER BY started_at DESC LIMIT 1').first();
    const success=await env.REPORT_DB.prepare("SELECT id,finished_at,body FROM cloud_pulse WHERE status='success' ORDER BY started_at DESC LIMIT 1").first();
    return response({latest,success},200,'application/json; charset=utf-8',{'X-Goose-CSRF':await logoutToken(request,env,url)});
  }
  if(url.pathname==='/investment/api/research-refresh'&&request.method==='POST'){
    if(!equal(request.headers.get('X-Goose-CSRF'),await logoutToken(request,env,url)))return response({error:'RESEARCH_CSRF_REJECTED'},403);
    return response(await research(env,'authenticated-refresh'));
  }
  if(url.pathname==='/investment/api/cloud-report'&&request.method==='GET'){
    const latest=await env.REPORT_DB.prepare('SELECT id,status,started_at,finished_at FROM cloud_holding_reports ORDER BY started_at DESC LIMIT 1').first();
    const success=await env.REPORT_DB.prepare("SELECT id,finished_at,body FROM cloud_holding_reports WHERE status='success' ORDER BY started_at DESC LIMIT 1").first();
    return response({latest,success},200,'application/json; charset=utf-8',{'X-Goose-CSRF':await logoutToken(request,env,url)});
  }
  if(url.pathname==='/investment/api/cloud-market'&&request.method==='GET')return response(await scanView(env),200,'application/json; charset=utf-8',{'X-Goose-CSRF':await logoutToken(request,env,url)});
  if(url.pathname==='/investment/api/cloud-signals'&&request.method==='GET'){
    const counts=(await env.REPORT_DB.prepare('SELECT cohort,COUNT(DISTINCT event_id) AS events FROM cloud_signal_versions GROUP BY cohort').all()).results;
    const outcomes=(await env.REPORT_DB.prepare('SELECT report_id,evaluated_at,body FROM cloud_signal_outcomes ORDER BY evaluated_at DESC LIMIT 30').all()).results;
    const tracking=await env.REPORT_DB.prepare('SELECT * FROM cloud_tracking_runs ORDER BY at DESC LIMIT 1').first();
    return response({counts,outcomes,tracking,meaning:'前瞻、重建、模拟严格分开；后验只有云端已验证原始日线覆盖的持仓标的，非交易净收益。'});
  }
  if(url.pathname==='/investment/api/scan-batch'&&request.method==='POST'){
    if(!equal(request.headers.get('X-Goose-CSRF'),await logoutToken(request,env,url)))return response({error:'SCAN_CSRF_REJECTED'},403);
    return response(await scanBatch(env,'authenticated-batch'));
  }
  if (request.method !== 'GET') return response({error:'READ_ONLY_CLOUD'},405,'application/json; charset=utf-8',{Allow:'GET'});
  if (url.pathname === '/investment/api/snapshot') {
    const row = await env.REPORT_DB.prepare('SELECT body FROM private_snapshot WHERE id=1').first();
    const headers={'X-Goose-CSRF':await logoutToken(request,env,url)};
    return row ? response(row.body,200,'application/json; charset=utf-8',headers) : response({error:'NO_SYNCED_REPORT'},404,'application/json; charset=utf-8',headers);
  }
  if (url.pathname === '/') return response('',303,'text/plain',{Location:'/investment/'});
  if (!assetPaths.has(url.pathname)) return response({error:'NOT_FOUND'},404);
  const asset = await env.ASSETS.fetch(request);
  return new Response(asset.body,{status:asset.status,headers:{...Object.fromEntries(asset.headers),...SECURITY}});
}
export default {async fetch(request,env) {
  try { return await handle(request,env); } catch { return serviceFailure(request,'PRIVATE_SERVICE_UNAVAILABLE'); }
},async scheduled(event,env,ctx){ctx.waitUntil(event.cron==='38 6 * * 1-5'?collectPulse(env,'scheduled'):event.cron.includes('0,5,10,15,20,25,30,35')?scanBatch(env,'scheduled'):research(env,'scheduled'));}};

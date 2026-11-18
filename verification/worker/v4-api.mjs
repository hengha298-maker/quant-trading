// Candidate cloud protocol. No brokerage or notification network calls.
const te = new TextEncoder(), td = new TextDecoder("utf-8", { fatal: true });
const LIMIT = 192 * 1024, CHUNK = 96 * 1024, TOTAL = 64 * 1024 * 1024;
const HEX = /^[a-f0-9]{64}$/, ID = /^[A-Za-z0-9_-]{1,100}$/, RID = /^[A-Za-z0-9_:.\/-]{1,160}$/;
const STATES = ["PENDING", "RUNNING", "GENERATED", "UPLOADING", "VERIFIED", "NOTIFIED", "FAILED", "STALE"];
class ApiError extends Error {
  constructor(code, status = 400, extra = {}) {
    super(code);
    this.status = status;
    this.extra = extra;
  }
}
const fail = (code, status = 400, extra) => {
  throw new ApiError(code, status, extra);
};
const stamp = (now) => new Date(now * 1e3).toISOString();
const plain = (v) => v && typeof v === "object" && !Array.isArray(v);
const hex = (v) => Array.from(new Uint8Array(v), (b) => b.toString(16).padStart(2, "0")).join("");
async function sha(v) {
  return hex(await crypto.subtle.digest("SHA-256", v));
}
function equal(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let n = 0;
  for (let i = 0; i < a.length; i++) n |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return n === 0;
}
function b64(v) {
  let s = "";
  for (let i = 0; i < v.length; i += 8192) s += String.fromCharCode(...v.subarray(i, i + 8192));
  return btoa(s);
}
function unb64(v) {
  if (typeof v !== "string" || v.length > 14e4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(v)) fail("BASE64_REJECTED");
  try {
    return Uint8Array.from(atob(v), (x) => x.charCodeAt(0));
  } catch {
    fail("BASE64_REJECTED");
  }
}
function proofKey(env) {
  const value = env.RUNNER_PROOF_SECRET || env.SESSION_SECRET;
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(value || "")) fail("SERVER_PROOF_KEY_NOT_CONFIGURED", 503);
  return value;
}
async function mac(key, value) {
  const k = await crypto.subtle.importKey("raw", te.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64(new Uint8Array(await crypto.subtle.sign("HMAC", k, te.encode(value)))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}
function canonical(v) {
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  if (v && typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonical(v[k])).join(",") + "}";
  return JSON.stringify(v);
}
function json(v) {
  try {
    return JSON.parse(td.decode(v));
  } catch {
    fail("JSON_REJECTED");
  }
}
function clean(v, depth = 0) {
  if (depth > 32) fail("JSON_DEPTH_REJECTED");
  if (typeof v === "number" && !Number.isFinite(v)) fail("NUMBER_REJECTED");
  if (typeof v === "string") {
    if (v.length > 131072) fail("STRING_REJECTED");
    if (/\b(?:Bearer|Basic|Goose-HMAC|Goose-Runner)\s+[A-Za-z0-9+/_=-]{8,}/i.test(v) || v.includes("-----BEGIN PRIVATE KEY-----") || v.includes("#key=") || /[?&](?:access_token|token|api_?key|password|secret|device_key)=/i.test(v)) fail("CREDENTIAL_CONTENT_REJECTED");
  }
  if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      const n = k.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (/password|passphrase|credential|secret/.test(n) || ["authorization", "cookie", "setcookie", "token", "accesstoken", "refreshtoken", "uploadtoken", "sessiontoken", "sessionid", "bearertoken", "apikey", "xapikey", "devicekey", "barkkey", "uploadkey", "uploadkeyprotected", "accesskey", "accesskeyid", "privatekey", "signingkey"].includes(n)) fail("CREDENTIAL_FIELD_REJECTED");
      clean(x, depth + 1);
    }
  }
}
function date(v) {
  if (typeof v !== "string" || !/^\d{4}-\d\d-\d\d(?:T.*(?:Z|[+-]\d\d:\d\d))?$/.test(v) || !Number.isFinite(Date.parse(v))) fail("DATE_REJECTED");
  return v;
}
function path(v) {
  if (typeof v !== "string" || v.length > 240 || v.includes("\\") || v.includes("//") || v.split("/").some((p) => !p || p === "." || p === ".." || /[<>:"|?*\x00-\x1f]/.test(p) || /[. ]$/.test(p) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p)) || !v.endsWith(".json") && !/^corporate-actions\/.+\.(pdf|html|txt)$/i.test(v)) fail("INPUT_PATH_REJECTED");
  if (!/^(private\/(holdings|broker-events|broker-event-voids|ledger-statements|recovered-ledgers)|runs|radar|signals|research-history-v2|research-events|research-event-evidence|research-outcomes|corporate-actions|market-discovery)\//.test(v) || /bark|credential|secret|config|auth|task|claim/i.test(v)) fail("INPUT_NAMESPACE_REJECTED");
  return v;
}
function descriptor(v) {
  if (!plain(v) || !HEX.test(v.sha256) || !HEX.test(v.storageSha256) || !Number.isInteger(v.byteLength) || v.byteLength < 1 || v.byteLength > 8 * 1024 * 1024 || !Number.isInteger(v.storageBytes) || v.storageBytes < 1 || v.storageBytes > 8 * 1024 * 1024 || !["identity", "gzip"].includes(v.storageEncoding) || !Number.isInteger(v.chunkCount) || v.chunkCount < 1 || v.chunkCount > 128 || v.storageBytes > v.chunkCount * CHUNK) fail("OBJECT_MANIFEST_REJECTED");
  return v;
}
async function rawBody(request) {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > LIMIT)) fail("BODY_LIMIT", 413);
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  let n = 0, all = [];
  while (true) {
    const r = await reader.read();
    if (r.done) break;
    n += r.value.length;
    if (n > LIMIT) {
      await reader.cancel();
      fail("BODY_LIMIT", 413);
    }
    all.push(r.value);
  }
  const v = new Uint8Array(n);
  let p = 0;
  for (const x of all) {
    v.set(x, p);
    p += x.length;
  }
  return v;
}
function out(v, status = 200, headers = {}) {
  return new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store, private", ...headers } });
}
const q = (db, sql, ...args) => db.prepare(sql).bind(...args);
async function generation(db) {
  return (await q(db, "SELECT input_version AS v FROM v4c_control WHERE id=1").first()).v;
}
async function lease(db, b, now) {
  if (!plain(b) || typeof b.jobKey !== "string" || typeof b.leaseToken !== "string") fail("LEASE_REQUIRED", 401);
  const row = await q(db, "SELECT * FROM v4c_jobs WHERE job_key=?", b.jobKey).first();
  if (!row || row.status !== "running" || row.lease_until <= now || !equal(row.lease_digest, await sha(te.encode(b.leaseToken)))) fail("LEASE_EXPIRED_OR_INVALID", 409);
  return row;
}
function cas(db, expected, now, reason, job = null) {
  if (!Number.isInteger(expected) || expected < 0) fail("INPUT_VERSION_REQUIRED");
  const condition = job ? " AND EXISTS(SELECT 1 FROM v4c_jobs WHERE job_key=? AND lease_digest=? AND lease_until>? AND status='running')" : "";
  const args = [expected, ...job ? [job.job_key, job.lease_digest, now] : []];
  return [q(db, `UPDATE v4c_control SET input_version=CASE WHEN input_version=?${condition} THEN input_version+1 ELSE -1 END WHERE id=1`, ...args), q(db, "INSERT INTO v4c_input_generations VALUES(?,?,?)", expected + 1, stamp(now), reason), q(db, "INSERT INTO v4c_input_files SELECT ?,path,object_id FROM v4c_input_files WHERE version=?", expected + 1, expected)];
}
function fence(db, job, now, { version, latest, eligibleLatest, state: state2 = job.state, accepted } = {}) {
  const terms = ["EXISTS(SELECT 1 FROM v4c_jobs WHERE job_key=? AND lease_digest=? AND lease_until>? AND status='running'" + (state2 ? " AND state=?" : "") + ")"], args = [job.job_key, job.lease_digest, now, ...state2 ? [state2] : []];
  if (version !== void 0) {
    terms.push("input_version=?");
    args.push(version);
  }
  if (latest) {
    terms.push("EXISTS(SELECT 1 FROM v4c_latest WHERE id=1 AND report_id=? AND sha256=?)");
    args.push(latest.reportId, latest.sha256);
  }
  if (eligibleLatest) {
    terms.push("NOT EXISTS(SELECT 1 FROM v4c_latest WHERE id=1 AND (generated_at>? OR generated_at=? AND report_id!=?))");
    args.push(eligibleLatest.generated_at, eligibleLatest.generated_at, eligibleLatest.report_id);
  }
  if (accepted) {
    terms.push("EXISTS(SELECT 1 FROM v4c_notifications WHERE job_key=? AND report_id=? AND sha256=? AND status='accepted')");
    args.push(job.job_key, accepted.reportId, accepted.sha256);
  }
  return q(db, "UPDATE v4c_control SET input_version=CASE WHEN " + terms.join(" AND ") + " THEN input_version ELSE -1 END WHERE id=1", ...args);
}
async function atomic(db, statements) {
  try {
    return await db.batch(statements);
  } catch (e) {
    if (/constraint|unique|CHECK/i.test(String(e))) fail("VERSION_CONFLICT", 409);
    throw e;
  }
}
async function objectBytes(db, id) {
  const o = await q(db, "SELECT * FROM v4c_objects WHERE object_id=? AND complete=1", id).first();
  if (!o) fail("OBJECT_NOT_COMMITTED", 404);
  const rows = (await q(db, "SELECT chunk_index,sha256,body FROM v4c_chunks WHERE object_id=? ORDER BY chunk_index", id).all()).results;
  if (rows.length !== o.chunk_count) fail("OBJECT_CHUNKS_MISSING", 409);
  let n = 0, parts = [];
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i], v = new Uint8Array(r.body);
    if (r.chunk_index !== i || await sha(v) !== r.sha256) fail("CHUNK_INTEGRITY_FAILED", 409);
    n += v.length;
    parts.push(v);
  }
  if (n !== o.storage_bytes) fail("STORAGE_LENGTH_MISMATCH", 409);
  const compressed = new Uint8Array(n);
  let offset = 0;
  for (const p of parts) {
    compressed.set(p, offset);
    offset += p.length;
  }
  if (await sha(compressed) !== o.storage_sha256) fail("STORAGE_HASH_MISMATCH", 409);
  let raw = compressed;
  if (o.storage_encoding === "gzip") {
    const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip")), reader = stream.getReader();
    let size = 0, pieces = [];
    try {
      while (true) {
        const r = await reader.read();
        if (r.done) break;
        size += r.value.length;
        if (size > o.byte_length || size > 8 * 1024 * 1024) {
          await reader.cancel();
          fail("DECOMPRESSION_LIMIT", 413);
        }
        pieces.push(r.value);
      }
    } catch (e) {
      if (e instanceof ApiError) throw e;
      fail("GZIP_REJECTED", 409);
    }
    raw = new Uint8Array(size);
    let p = 0;
    for (const v of pieces) {
      raw.set(v, p);
      p += v.length;
    }
  }
  if (raw.length !== o.byte_length || await sha(raw) !== o.sha256) fail("OBJECT_HASH_MISMATCH", 409);
  return { raw, object: o };
}
function recordView(r) {
  return { recordId: r.record_id, revision: r.revision, kind: r.kind, payload: JSON.parse(r.payload_json), sha256: r.sha256, voided: !!r.voided, sourcePath: r.source_path, sourceAt: r.source_at, dataStatus: r.data_status, reviewStatus: r.review_status, updatedAt: r.updated_at, inputVersion: r.input_version };
}
async function records(db) {
  return (await q(db, "SELECT r.* FROM v4c_records r JOIN (SELECT record_id,MAX(revision) AS rev FROM v4c_records GROUP BY record_id) h ON r.record_id=h.record_id AND r.revision=h.rev ORDER BY r.updated_at DESC LIMIT 500").all()).results.map(recordView);
}
async function reports(db) {
  return (await q(db, "SELECT r.*,o.byte_length FROM v4c_reports r JOIN v4c_objects o ON o.object_id=r.object_id ORDER BY generated_at DESC LIMIT 100").all()).results.map((r) => ({ reportId: r.report_id, generatedAt: r.generated_at, asOf: r.as_of, sha256: r.sha256, bytes: r.byte_length, sourceStatus: r.source_status, inputVersion: r.input_version, resultVersion: r.result_version }));
}
async function runs(db) {
  return (await q(db, "SELECT j.job_key AS jobKey,j.run_id AS runId,j.state,j.status,j.report_id AS reportId,j.started_at AS startedAt,j.updated_at AS updatedAt,j.code,j.attempt,j.verified_at AS verifiedAt,j.readback_scope AS readbackScope,n.status AS notificationStatus,n.updated_at AS notificationUpdatedAt,n.result_json AS notificationJson FROM v4c_jobs j LEFT JOIN v4c_notifications n ON n.job_key=j.job_key ORDER BY j.updated_at DESC LIMIT 100").all()).results.map(({ notificationJson, ...r }) => ({ ...r, notification: notificationJson ? JSON.parse(notificationJson) : null, serviceAcceptedAt: r.notificationStatus === "accepted" ? r.notificationUpdatedAt : null, phoneArrivalVerified: false }));
}
async function validateRecord(b, now) {
  if (!plain(b) || !RID.test(b.recordId) || !["holdings-snapshot", "wealth-entry", "settings"].includes(b.kind) || !Number.isInteger(b.expectedRevision) || b.expectedRevision < 0 || !["save", "void"].includes(b.action || "save")) fail("RECORD_REJECTED");
  if ((b.action || "save") === "void") return;
  const p = b.payload;
  if (!plain(p)) fail("PAYLOAD_REQUIRED");
  clean(p);
  if (b.kind === "wealth-entry") {
    if (p.recordType !== "wealth-entry" || typeof p.name !== "string" || !p.name.trim() || p.name.length > 100 || !["cash", "deposit", "investment", "property", "other-asset", "liability"].includes(p.category) || typeof p.amountCents !== "string" || !/^\d{1,18}$/.test(p.amountCents) || p.currency !== "CNY") fail("WEALTH_FIELDS_REJECTED");
    date(p.asOf);
    if (p.asOf.slice(0, 10) > new Date(now * 1e3 + 8 * 36e5).toISOString().slice(0, 10)) fail("FUTURE_AS_OF");
  }
  if (b.kind === "holdings-snapshot") {
    if (p.format !== "goose-holdings-snapshot" || p.version !== 1 || !ID.test(p.snapshotId) || b.recordId !== "holdings:" + p.snapshotId || p.confirmation !== "user-reviewed-preview" || !plain(p.source) || !["manual", "csv", "xlsx"].includes(p.source.kind) || !["华宝证券", "银河证券"].includes(p.source.broker) || !HEX.test(p.sourceSha256) || !Array.isArray(p.positions) || p.positions.length < 1 || p.positions.length > 500 || p.positionsCount !== p.positions.length) fail("HOLDINGS_FIELDS_REJECTED");
    date(p.asOf);
    date(p.confirmedAt);
    date(p.generatedAt);
    if (Date.parse(p.confirmedAt) > now * 1e3 + 6e4 || p.asOf.slice(0, 10) > new Date(now * 1e3 + 8 * 36e5).toISOString().slice(0, 10)) fail("FUTURE_AS_OF");
    const seen = /* @__PURE__ */ new Set();
    for (const x of p.positions) {
      if (!plain(x) || !/^\d{6}\.(SH|SZ)$/.test(x.symbol) || seen.has(x.symbol) || !Number.isSafeInteger(x.quantity) || x.quantity < 0 || x.quantity > 1e12 || typeof x.name !== "string" || x.name.length > 80 || x.costPrice !== null && (typeof x.costPrice !== "string" || !/^\d+(\.\d{1,8})?$/.test(x.costPrice))) fail("HOLDINGS_POSITION_REJECTED");
      seen.add(x.symbol);
    }
  }
  if (b.kind === "holdings-snapshot") {
    const preview = { ...p, format: "goose-holdings-preview" };
    for (const k of ["snapshotId", "confirmedAt", "confirmation", "previewSha256"]) delete preview[k];
    if (!HEX.test(p.previewSha256 || "") || await sha(te.encode(canonical(preview))) !== p.previewSha256) fail("HOLDINGS_PREVIEW_HASH_MISMATCH");
    if (p.availableCash !== null && (typeof p.availableCash !== "string" || !/^\d+(\.\d{1,8})?$/.test(p.availableCash))) fail("AVAILABLE_CASH_REJECTED");
  }
  if (b.kind === "settings" && Object.keys(p).some((k) => !["recordType", "reportTime", "barkPreference", "watchlist", "notificationPreview", "theme", "note"].includes(k))) fail("SETTING_NOT_EDITABLE");
  if (b.kind === "settings" && (p.reportTime !== void 0 && p.reportTime !== "14:38" || p.barkPreference !== void 0 && !["critical-only", "off"].includes(p.barkPreference) || p.watchlist !== void 0 && (!Array.isArray(p.watchlist) || p.watchlist.length > 100 || p.watchlist.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x))) || p.notificationPreview !== void 0 && p.notificationPreview !== true)) fail("SETTINGS_VALUE_REJECTED");
}
async function saveRecord(db, b, now) {
  await validateRecord(b, now);
  const old = await q(db, "SELECT * FROM v4c_records WHERE record_id=? ORDER BY revision DESC LIMIT 1", b.recordId).first();
  if ((old?.revision || 0) !== b.expectedRevision) fail("VERSION_CONFLICT", 409, { currentRevision: old?.revision || 0 });
  const voided = b.action === "void";
  if (voided && !old) fail("RECORD_NOT_FOUND", 404);
  if (old && old.kind !== b.kind) fail("RECORD_KIND_CONFLICT", 409);
  if (old && b.kind === "holdings-snapshot" && !voided) fail("HOLDINGS_APPEND_NEW_SNAPSHOT", 409);
  const payload = voided ? JSON.parse(old.payload_json) : b.payload, raw = te.encode(JSON.stringify(payload));
  if (raw.length > 131072) fail("RECORD_LIMIT", 413);
  const digest = await sha(raw), version = await generation(db), sourcePath = b.kind === "holdings-snapshot" ? "private/holdings/" + payload.snapshotId + ".json" : null;
  const statements = cas(db, version, now, "browser-" + b.kind);
  if (sourcePath) {
    statements.push(q(db, "DELETE FROM v4c_input_files WHERE version=? AND path=?", version + 1, sourcePath));
    if (!voided) {
      const oid = crypto.randomUUID();
      statements.push(q(db, "INSERT INTO v4c_objects VALUES(?,?,?,?,?,?,?,1,?)", oid, digest, raw.length, "identity", digest, raw.length, 1, "json"), q(db, "INSERT INTO v4c_chunks VALUES(?,?,?,?)", oid, 0, digest, raw), q(db, "INSERT INTO v4c_input_files VALUES(?,?,?)", version + 1, sourcePath, oid));
    }
  }
  statements.push(q(db, "INSERT INTO v4c_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", b.recordId, b.expectedRevision + 1, b.kind, td.decode(raw), digest, voided ? 1 : 0, sourcePath, b.sourceAt || payload.asOf || null, "historical", "user-asserted-awaiting-reconciliation", stamp(now), version + 1));
  await atomic(db, statements);
  return recordView(await q(db, "SELECT * FROM v4c_records WHERE record_id=? AND revision=?", b.recordId, b.expectedRevision + 1).first());
}
async function readReport(db, id) {
  if (!ID.test(id)) fail("REPORT_ID_REJECTED");
  const r = await q(db, "SELECT * FROM v4c_reports WHERE report_id=?", id).first();
  if (!r) fail("REPORT_NOT_FOUND", 404);
  const { raw } = await objectBytes(db, r.object_id), doc = json(raw), report = doc.report || doc;
  if (report.id !== r.report_id || Date.parse(report.generatedAt) !== Date.parse(r.generated_at) || report.asOf !== r.as_of || await sha(raw) !== r.sha256) fail("REPORT_INDEX_MISMATCH", 409);
  return { row: r, raw, doc };
}
async function browserApi(request, env, now, csrf) {
  try {
    const db = env.REPORT_DB, url = new URL(request.url), base = "/investment/api/v4/", route = url.pathname.slice(base.length), headers = { "X-Goose-CSRF": csrf };
    if (!["GET", "POST"].includes(request.method)) fail("METHOD_REJECTED", 405);
    if (request.method === "POST") {
      if (request.headers.get("Content-Type")?.split(";")[0] !== "application/json") fail("JSON_CONTENT_TYPE_REQUIRED", 415);
      if (request.headers.get("Origin") && request.headers.get("Origin") !== url.origin || !equal(request.headers.get("X-Goose-CSRF"), csrf)) fail("CSRF_REJECTED", 403);
      const b = json(await rawBody(request));
      if (route === "records" || route === "settings") {
        if (route === "settings") {
          b.recordId = "settings";
          b.kind = "settings";
        }
        return out(await saveRecord(db, b, now), 200, headers);
      }
      fail("NOT_FOUND", 404);
    }
    if (route === "bootstrap") {
      const rs = await records(db), latest = await q(db, "SELECT r.* FROM v4c_latest l JOIN v4c_reports r ON l.report_id=r.report_id WHERE l.id=1").first(), currentVersion = await generation(db);
      return out({ version: 1, csrfToken: csrf, records: rs, reports: await reports(db), report: latest ? (await readReport(db, latest.report_id)).doc : null, reportBinding: latest ? { inputVersion: latest.input_version, resultVersion: latest.result_version, currentInputVersion: currentVersion, matchesCurrentInputs: latest.result_version === currentVersion } : null, runs: await runs(db), settings: rs.find((r) => r.kind === "settings" && !r.voided)?.payload || { notificationPreview: true }, inputVersion: currentVersion }, 200, headers);
    }
    if (route === "records" || route === "settings") {
      const id = url.searchParams.get("recordId");
      if (id) {
        if (!RID.test(id)) fail("RECORD_ID_REJECTED");
        const history = (await q(db, "SELECT * FROM v4c_records WHERE record_id=? ORDER BY revision DESC LIMIT 100", id).all()).results.map(recordView);
        return out({ records: history }, 200, headers);
      }
      return out({ records: await records(db) }, 200, headers);
    }
    if (route === "reports") return out({ reports: await reports(db) }, 200, headers);
    if (route.startsWith("reports/")) return out((await readReport(db, route.slice(8))).doc, 200, headers);
    if (route === "runs") return out({ runs: await runs(db) }, 200, headers);
    fail("NOT_FOUND", 404);
  } catch (e) {
    if (e instanceof ApiError) return out({ error: e.message, ...e.extra }, e.status, { "X-Goose-CSRF": csrf });
    return out({ error: "V4_DATABASE_OR_RUNTIME_FAILED" }, 503);
  }
}
async function authenticate(request, env, now) {
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(env.RUNNER_SECRET || "")) fail("RUNNER_NOT_CONFIGURED", 503);
  const t = request.headers.get("X-Goose-Time") || "", nonce = request.headers.get("X-Goose-Nonce") || "", digest = request.headers.get("X-Goose-SHA256") || "", url = new URL(request.url);
  if (url.search || !/^\d{10}$/.test(t) || Math.abs(Number(t) - now) > 300 || !/^[A-Za-z0-9_-]{16,80}$/.test(nonce) || !HEX.test(digest)) fail("RUNNER_AUTH_REQUIRED", 401);
  const raw = await rawBody(request);
  if (await sha(raw) !== digest || !equal(request.headers.get("Authorization"), "Goose-Runner " + await mac(env.RUNNER_SECRET, `${request.method}
${url.pathname}
${t}
${nonce}
${digest}`))) fail("RUNNER_AUTH_REQUIRED", 401);
  try {
    await env.REPORT_DB.batch([q(env.REPORT_DB, "DELETE FROM v4c_nonces WHERE expires<?", now), q(env.REPORT_DB, "INSERT INTO v4c_nonces VALUES(?,?)", nonce, now + 600)]);
  } catch {
    fail("RUNNER_REPLAY_REJECTED", 409);
  }
  if (request.method === "POST" && request.headers.get("Content-Type")?.split(";")[0] !== "application/json") fail("JSON_CONTENT_TYPE_REQUIRED", 415);
  return raw.length ? json(raw) : {};
}
async function beginUpload(db, b, now, job, seed = false) {
  const expected = b.expectedBundleVersion;
  if (!Number.isInteger(expected) || expected < 0 || await generation(db) !== expected) fail("INPUT_VERSION_CONFLICT", 409);
  let files = seed ? b.files : [b.report, ...b.artifacts || []];
  if (!Array.isArray(files) || files.length < 1 || files.length > 5e3) fail("UPLOAD_FILES_REJECTED");
  let total = 0, seen = /* @__PURE__ */ new Set();
  files = files.map((v, i) => {
    descriptor(v);
    total += v.byteLength;
    const p = seed || i > 0 ? path(v.path) : null;
    if (!seed && p?.startsWith("private/")) fail("FINANCIAL_ORIGINAL_IMMUTABLE");
    if (p && seen.has(p.toLowerCase())) fail("DUPLICATE_INPUT_PATH");
    if (p) seen.add(p.toLowerCase());
    return { ...v, path: p };
  });
  if (total > TOTAL) fail("UPLOAD_TOTAL_LIMIT", 413);
  if (!seed && (!ID.test(b.reportId) || !HEX.test(b.sha256) || files[0].sha256 !== b.sha256)) fail("REPORT_MANIFEST_REJECTED");
  const manifest = { files, seed, leaseDigest: job?.lease_digest || null, reportId: seed ? null : b.reportId, sha256: seed ? null : b.sha256 }, text = JSON.stringify(manifest);
  if (te.encode(text).length > LIMIT) fail("MANIFEST_LIMIT", 413);
  const digest = await sha(te.encode(text));
  const existing = await q(db, "SELECT upload_id,status,manifest_sha256 FROM v4c_uploads WHERE job_key IS ? AND report_id IS ? AND expected_version=? ORDER BY created_at DESC LIMIT 1", job?.job_key || null, manifest.reportId, expected).first();
  if (existing && existing.manifest_sha256 === digest) return { status: existing.status === "committed" ? "already-stored" : "ready", uploadId: existing.upload_id };
  const id = crypto.randomUUID(), statements = [q(db, "INSERT INTO v4c_uploads VALUES(?,?,?,?,?,?,?,?,?)", id, job?.job_key || null, expected, text, digest, "open", stamp(now), manifest.reportId, manifest.sha256)];
  for (let i = 0; i < files.length; i++) {
    const f = files[i], oid = crypto.randomUUID();
    statements.push(q(db, "INSERT INTO v4c_objects VALUES(?,?,?,?,?,?,?,0,?)", oid, f.sha256, f.byteLength, f.storageEncoding, f.storageSha256, f.storageBytes, f.chunkCount, f.contentKind || "json"), q(db, "INSERT INTO v4c_upload_items VALUES(?,?,?,?)", id, i, oid, f.path));
  }
  await atomic(db, statements);
  return { status: "ready", uploadId: id };
}
async function uploadRow(db, b, job, seed) {
  if (!ID.test(b.uploadId)) fail("UPLOAD_ID_REJECTED");
  const row = await q(db, "SELECT * FROM v4c_uploads WHERE upload_id=?", b.uploadId).first();
  if (!row || row.job_key !== (job?.job_key || null) || JSON.parse(row.manifest_json).seed !== seed || !seed && JSON.parse(row.manifest_json).leaseDigest !== job.lease_digest) fail("UPLOAD_NOT_FOUND", 404);
  return row;
}
async function chunkUpload(db, b, job, seed) {
  const u = await uploadRow(db, b, job, seed);
  if (u.status !== "open") fail("UPLOAD_CLOSED", 409);
  if (!Number.isInteger(b.fileIndex) || !Number.isInteger(b.chunkIndex) || b.chunkIndex < 0 || !HEX.test(b.sha256)) fail("CHUNK_FIELDS_REJECTED");
  const o = await q(db, "SELECT o.* FROM v4c_upload_items i JOIN v4c_objects o ON o.object_id=i.object_id WHERE i.upload_id=? AND i.file_index=?", b.uploadId, b.fileIndex).first();
  if (!o || b.chunkIndex >= o.chunk_count) fail("CHUNK_INDEX_REJECTED");
  const raw = unb64(b.contentBase64);
  if (!raw.length || raw.length > CHUNK || await sha(raw) !== b.sha256) fail("CHUNK_HASH_REJECTED");
  const old = await q(db, "SELECT sha256 FROM v4c_chunks WHERE object_id=? AND chunk_index=?", o.object_id, b.chunkIndex).first();
  if (old) {
    if (old.sha256 !== b.sha256) fail("CHUNK_CONFLICT", 409);
    return { status: "already-stored" };
  }
  await q(db, "INSERT INTO v4c_chunks VALUES(?,?,?,?)", o.object_id, b.chunkIndex, b.sha256, raw).run();
  const count = await q(db, "SELECT COUNT(*) AS n FROM v4c_chunks WHERE object_id=?", o.object_id).first();
  if (count.n === o.chunk_count) {
    await q(db, "UPDATE v4c_objects SET complete=1 WHERE object_id=?", o.object_id).run();
    try {
      const checked = await objectBytes(db, o.object_id), item = await q(db, "SELECT path FROM v4c_upload_items WHERE upload_id=? AND file_index=?", b.uploadId, b.fileIndex).first();
      if (!item.path || item.path.endsWith(".json")) clean(json(checked.raw));
    } catch (e) {
      await q(db, "UPDATE v4c_objects SET complete=0 WHERE object_id=?", o.object_id).run();
      throw e;
    }
  }
  return { status: "stored" };
}
async function materializedHoldings(db, version, now) {
  const files = (await q(db, "SELECT f.path,f.object_id FROM v4c_input_files f WHERE version=? AND path LIKE 'private/holdings/%' ORDER BY path LIMIT 11", version).all()).results;
  if (files.length > 10) fail("HOLDINGS_MATERIALIZE_PAGE_REQUIRED", 409);
  const statements = [];
  for (const f of files) {
    const checked = await objectBytes(db, f.object_id), p = json(checked.raw);
    if (p.format !== "goose-holdings-snapshot" || p.version !== 1 || !ID.test(p.snapshotId) || p.confirmation !== "user-reviewed-preview") fail("IMPORTED_HOLDINGS_REJECTED", 409);
    const id = "holdings:" + p.snapshotId, old = await q(db, "SELECT sha256 FROM v4c_records WHERE record_id=? ORDER BY revision DESC LIMIT 1", id).first();
    if (old) {
      if (old.sha256 !== checked.object.sha256) fail("IMPORTED_HOLDINGS_CONFLICT", 409);
      continue;
    }
    statements.push(q(db, "INSERT INTO v4c_records VALUES(?,1,?,?,?,?,?,?,?,?,?,?)", id, "holdings-snapshot", td.decode(checked.raw), checked.object.sha256, 0, f.path, p.asOf || null, "historical", "source-preserved-awaiting-reconciliation", stamp(now), version));
  }
  return statements;
}
async function commitUpload(db, b, now, job, seed) {
  const u = await uploadRow(db, b, job, seed), manifest = JSON.parse(u.manifest_json);
  if (u.status === "committed") return { status: "already-stored", reportId: u.report_id, sha256: u.report_sha256, bundleVersion: u.expected_version + 1 };
  const items = (await q(db, "SELECT i.*,o.* FROM v4c_upload_items i JOIN v4c_objects o ON i.object_id=o.object_id WHERE i.upload_id=? ORDER BY i.file_index", b.uploadId).all()).results;
  if (items.length !== manifest.files.length) fail("UPLOAD_INCOMPLETE", 409);
  let report = null;
  if (items.some((x) => x.complete !== 1)) fail("UPLOAD_OBJECT_NOT_VERIFIED", 409);
  if (!seed) {
    const { raw } = await objectBytes(db, items[0].object_id), d = json(raw);
    report = d.report || d;
    if (d.format !== "goose-private-cloud-snapshot" || d.version !== 1 || report.format !== "goose-live-research" || report.version !== 1 || report.id !== u.report_id || report.synthetic || report.testData || report.cachedReport || !/^\d{4}-\d\d-\d\d$/.test(report.asOf) || report.asOf !== job.job_key.slice(6) || !Number.isFinite(Date.parse(report.generatedAt)) || Date.parse(report.generatedAt) > now * 1e3 + 5e3 || new Date(Date.parse(report.generatedAt) + 8 * 36e5).toISOString().slice(0, 10) !== report.asOf) fail("REPORT_PROVENANCE_REJECTED");
  }
  const version = u.expected_version, statements = cas(db, version, now, seed ? "seed-import" : "runner-report", job);
  for (const item of items) {
    if (item.path) statements.push(q(db, "DELETE FROM v4c_input_files WHERE version=? AND path=?", version + 1, item.path), q(db, "INSERT INTO v4c_input_files VALUES(?,?,?)", version + 1, item.path, item.object_id));
  }
  if (!seed) {
    const old = await q(db, "SELECT sha256 FROM v4c_reports WHERE report_id=?", u.report_id).first();
    if (old) {
      if (old.sha256 !== u.report_sha256) fail("REPORT_IMMUTABLE_CONFLICT", 409);
      fail("REPORT_ALREADY_COMMITTED_DIFFERENT_UPLOAD", 409);
    }
    statements.push(q(db, "INSERT INTO v4c_reports VALUES(?,?,?,?,?,?,?,?,?,?)", u.report_id, items[0].object_id, u.report_sha256, new Date(report.generatedAt).toISOString(), report.asOf, "historical-until-freshness-verified", stamp(now), job.job_key, version, version + 1), q(db, "UPDATE v4c_jobs SET report_id=?,sha256=?,input_version=?,updated_at=? WHERE job_key=?", u.report_id, u.report_sha256, version, stamp(now), job.job_key));
  }
  statements.push(q(db, "UPDATE v4c_uploads SET status='committed' WHERE upload_id=?", u.upload_id));
  await atomic(db, statements);
  return { status: "stored", reportId: u.report_id, sha256: u.report_sha256, bundleVersion: version + 1 };
}
async function state(db, b, job, env, now) {
  if (!STATES.includes(b.state)) fail("STATE_REJECTED");
  const terminal = ["FAILED", "STALE"], next = { PENDING: "RUNNING", RUNNING: "GENERATED", GENERATED: "UPLOADING", UPLOADING: "VERIFIED", VERIFIED: "NOTIFIED" };
  if (job.state === "NOTIFIED" && b.state !== "NOTIFIED" || !terminal.includes(b.state) && b.state !== job.state && next[job.state] !== b.state) fail("STATE_TRANSITION_REJECTED", 409);
  const id = b.reportId || job.report_id, digest = b.sha256 || job.sha256, statements = [fence(db, job, now)];
  if (["VERIFIED", "NOTIFIED"].includes(b.state)) {
    if (!id || !HEX.test(digest || "")) fail("REPORT_BINDING_REQUIRED");
    const r = await q(db, "SELECT * FROM v4c_reports WHERE report_id=? AND job_key=?", id, job.job_key).first();
    if (!r || r.sha256 !== digest) fail("REPORT_BINDING_CONFLICT", 409);
    if (b.state === "VERIFIED") {
      if (b.readbackSha256 !== digest || !equal(b.readbackProof, await mac(proofKey(env), `READBACK
${id}
${digest}`))) fail("COMPLETE_READBACK_PROOF_REQUIRED", 409);
      statements[0] = fence(db, job, now, { version: r.result_version, eligibleLatest: r });
      statements.push(q(db, "INSERT INTO v4c_latest VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET report_id=excluded.report_id,sha256=excluded.sha256,generated_at=excluded.generated_at,verified_at=excluded.verified_at,job_key=excluded.job_key", id, digest, r.generated_at, stamp(now), job.job_key), q(db, "UPDATE v4c_jobs SET verified_at=?,readback_scope='remote-complete-bytes' WHERE job_key=?", stamp(now), job.job_key));
    } else statements[0] = fence(db, job, now, { accepted: { reportId: id, sha256: digest } });
  }
  statements.push(q(db, "UPDATE v4c_jobs SET state=?,updated_at=?,code=? WHERE job_key=?", b.state, stamp(now), typeof b.code === "string" ? b.code.slice(0, 100) : null, job.job_key), q(db, "INSERT INTO v4c_events VALUES(?,?,?,?,?)", crypto.randomUUID(), job.job_key, b.state, stamp(now), typeof b.code === "string" ? b.code.slice(0, 100) : null));
  await atomic(db, statements);
  return { status: b.state, state: b.state };
}
function notificationFresh(report, now) {
  const local = new Date(now * 1e3 + 8 * 36e5).toISOString(), minute = Number(local.slice(11, 13)) * 60 + Number(local.slice(14, 16));
  if (minute < 878 || minute >= 884) fail("NOTIFICATION_OUTSIDE_ORIGINAL_WINDOW", 409);
  const day = new Date(now * 1e3 + 8 * 36e5).toISOString().slice(0, 10), generated = Date.parse(report?.generatedAt);
  if (report?.asOf !== day || !Number.isFinite(generated) || generated > now * 1e3 + 999 || !Array.isArray(report.instruments) || !report.instruments.length) fail("NOTIFICATION_REPORT_STALE_OR_MISSING", 409);
  const seen = /* @__PURE__ */ new Set();
  for (const item of report.instruments) {
    const q2 = item?.quote, source = Date.parse(q2?.sourceTimestamp), fetched = Date.parse(q2?.fetchedAt);
    if (!/^\d{6}\.(SH|SZ)$/.test(item?.symbol) || seen.has(item.symbol) || q2?.symbol !== item.symbol || !["tencent-quote", "eastmoney-minute"].includes(q2.source) || q2.adjustment !== "none" || q2.currency !== "CNY" || q2.priceUnit !== "CNY-per-share" || !(Number(q2.price) > 0) || !Number.isFinite(Number(q2.price)) || !Number.isFinite(source) || !Number.isFinite(fetched) || source > fetched || fetched > generated || now * 1e3 - source > 9e4 || now * 1e3 - fetched > 3e4 || q2.synthetic || q2.testData || q2.quality?.status !== "intraday-verified" || q2.quality?.unitVerified !== true || q2.quality?.intradaySignalAllowed !== true) fail("NOTIFICATION_QUOTE_STALE_OR_UNVERIFIED", 409);
    seen.add(item.symbol);
  }
}
async function runnerApi(request, env, now) {
  try {
    const db = env.REPORT_DB, url = new URL(request.url), route = url.pathname.slice("/api/runner/".length), b = await authenticate(request, env, now);
    if (request.method === "GET" && route.startsWith("reports/")) {
      const value = await readReport(db, route.slice(8));
      return new Response(value.raw, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store, private", "X-Goose-SHA256": value.row.sha256, "X-Goose-Report-Id": value.row.report_id, "X-Goose-Readback-Proof": await mac(proofKey(env), `READBACK
${value.row.report_id}
${value.row.sha256}`) } });
    }
    if (request.method !== "POST") fail("METHOD_REJECTED", 405);
    if (!plain(b)) fail("JSON_OBJECT_REQUIRED");
    if (route === "claim") {
      if (env.GOOSE_EXECUTION_OWNER !== "cloud") return out({ status: "not-claimed", code: "CLOUD_CUTOVER_NOT_AUTHORIZED" });
      if (!/^daily:\d{4}-\d\d-\d\d$/.test(b.jobKey) || !ID.test(b.runId) || !HEX.test(b.auditSha256) || b.leaseSeconds !== 180) fail("CLAIM_FIELDS_REJECTED");
      const old = await q(db, "SELECT * FROM v4c_jobs WHERE job_key=?", b.jobKey).first();
      if (old && (old.status === "completed" || old.lease_until > now && old.status === "running" || old.attempt >= 3)) return out({ status: "not-claimed", state: old.state });
      const token = b64(crypto.getRandomValues(new Uint8Array(32))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, ""), digest = await sha(te.encode(token));
      const r = await q(db, `INSERT INTO v4c_jobs(job_key,run_id,audit_sha256,lease_digest,lease_until,attempt,status,state,started_at,updated_at) VALUES(?,?,?,?,?,1,'running','RUNNING',?,?) ON CONFLICT(job_key) DO UPDATE SET run_id=excluded.run_id,audit_sha256=excluded.audit_sha256,lease_digest=excluded.lease_digest,lease_until=excluded.lease_until,attempt=v4c_jobs.attempt+1,status='running',state='RUNNING',report_id=NULL,sha256=NULL,input_version=NULL,verified_at=NULL,readback_scope=NULL,updated_at=excluded.updated_at WHERE v4c_jobs.status!='completed' AND (v4c_jobs.lease_until<=? OR v4c_jobs.status='failed') AND v4c_jobs.attempt<3`, b.jobKey, b.runId, b.auditSha256, digest, now + 180, stamp(now), stamp(now), now).run();
      return out(r.meta.changes ? { status: "claimed", leaseToken: token, attempt: (old?.attempt || 0) + 1 } : { status: "not-claimed" });
    }
    const seed = ["input-begin", "input-chunk-upload", "input-commit", "input-materialize"].includes(route) || route === "input-chunk" && typeof b.uploadId === "string";
    if (!seed && !["notification-result", "fail"].includes(route) && env.GOOSE_EXECUTION_OWNER !== "cloud") fail("CLOUD_OWNER_REVOKED", 409);
    const job = seed ? null : await lease(db, b, now);
    if (route === "input-materialize") {
      if (!Number.isInteger(b.expectedBundleVersion) || b.expectedBundleVersion !== await generation(db)) fail("INPUT_VERSION_CONFLICT", 409);
      const statements = await materializedHoldings(db, b.expectedBundleVersion, now);
      await atomic(db, [q(db, "UPDATE v4c_control SET input_version=CASE WHEN input_version=? THEN input_version ELSE -1 END WHERE id=1", b.expectedBundleVersion), ...statements]);
      return out({ status: "materialized", recordsAdded: statements.length, bundleVersion: b.expectedBundleVersion });
    }
    if (route === "heartbeat") {
      await q(db, "UPDATE v4c_jobs SET lease_until=?,updated_at=? WHERE job_key=? AND lease_digest=? AND status='running'", now + 180, stamp(now), job.job_key, job.lease_digest).run();
      return out({ status: "renewed" });
    }
    if (route === "inputs") {
      const v = job.input_version ?? await generation(db);
      if (job.input_version === null) await q(db, "UPDATE v4c_jobs SET input_version=? WHERE job_key=?", v, job.job_key).run();
      const cursor = b.cursor ?? 0;
      if (!Number.isInteger(cursor) || cursor < 0 || cursor > 5e3) fail("CURSOR_REJECTED");
      const rows = (await q(db, "SELECT f.path,o.* FROM v4c_input_files f JOIN v4c_objects o ON f.object_id=o.object_id WHERE f.version=? ORDER BY f.path LIMIT 21 OFFSET ?", v, cursor).all()).results;
      return out({ format: "goose-runner-input-manifest", version: 1, bundleVersion: v, files: rows.slice(0, 20).map((r) => ({ path: r.path, objectId: r.object_id, sha256: r.sha256, byteLength: r.byte_length, storageEncoding: r.storage_encoding, storageSha256: r.storage_sha256, storageBytes: r.storage_bytes, chunkCount: r.chunk_count, contentKind: r.content_kind })), nextCursor: rows.length > 20 ? cursor + 20 : null });
    }
    if (route === "input-chunk" && !seed) {
      if (b.bundleVersion !== job.input_version || !ID.test(b.objectId) || !Number.isInteger(b.chunkIndex)) fail("INPUT_CHUNK_REJECTED");
      const r = await q(db, "SELECT c.* FROM v4c_chunks c JOIN v4c_input_files f ON f.object_id=c.object_id WHERE f.version=? AND c.object_id=? AND c.chunk_index=? LIMIT 1", b.bundleVersion, b.objectId, b.chunkIndex).first();
      if (!r) fail("INPUT_CHUNK_NOT_FOUND", 404);
      return out({ contentBase64: b64(new Uint8Array(r.body)), sha256: r.sha256 });
    }
    if (route === "upload-begin" || route === "input-begin") return out(await beginUpload(db, b, now, job, seed));
    if (route === "upload-chunk" || route === "input-chunk-upload" || route === "input-chunk" && seed) return out(await chunkUpload(db, b, job, seed));
    if (route === "upload-commit" || route === "input-commit") return out(await commitUpload(db, b, now, job, seed));
    if (route === "state") return out(await state(db, b, job, env, now));
    if (route === "notification-claim") {
      if (job.state !== "VERIFIED" || job.report_id !== b.reportId || job.sha256 !== b.sha256) fail("VERIFIED_REPORT_REQUIRED", 409);
      const latest = await q(db, "SELECT report_id,sha256 FROM v4c_latest WHERE id=1").first();
      if (latest?.report_id !== b.reportId || latest?.sha256 !== b.sha256) fail("NOT_LATEST_VERIFIED_REPORT", 409);
      const reportRow = await q(db, "SELECT result_version FROM v4c_reports WHERE report_id=?", b.reportId).first();
      if (await generation(db) !== reportRow.result_version) fail("INPUT_CHANGED_BEFORE_NOTIFICATION", 409);
      const report = (await readReport(db, b.reportId)).doc.report;
      notificationFresh(report, now);
      const existing = await q(db, "SELECT status FROM v4c_notifications WHERE job_key=? OR report_id=?", job.job_key, b.reportId).first();
      if (existing) return out({ status: "not-claimed-no-resend", outcome: existing.status });
      const token = b64(crypto.getRandomValues(new Uint8Array(32))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
      try {
        await atomic(db, [fence(db, job, now, { version: reportRow.result_version, latest: { reportId: b.reportId, sha256: b.sha256 }, state: "VERIFIED" }), q(db, "INSERT INTO v4c_notifications VALUES(?,?,?,?,'claimed',?,?,NULL)", job.job_key, b.reportId, b.sha256, await sha(te.encode(token)), stamp(now), stamp(now))]);
      } catch (e) {
        const taken = await q(db, "SELECT status FROM v4c_notifications WHERE job_key=? OR report_id=?", job.job_key, b.reportId).first();
        if (taken) return out({ status: "not-claimed-no-resend", outcome: taken.status });
        throw e;
      }
      return out({ status: "claimed", notificationToken: token });
    }
    if (route === "notification-result") {
      const n = await q(db, "SELECT * FROM v4c_notifications WHERE job_key=?", job.job_key).first();
      if (!n || n.report_id !== b.reportId || n.sha256 !== b.sha256 || !equal(n.token_digest, await sha(te.encode(b.notificationToken || "")))) fail("NOTIFICATION_TOKEN_REJECTED", 403);
      if (n.status !== "claimed") return out({ status: "already-recorded", outcome: n.status });
      const r = b.result;
      if (!plain(r)) fail("NOTIFICATION_RESULT_REQUIRED");
      const accepted = r.sent === true && r.serviceAccepted === true && Number.isInteger(r.httpStatus) && r.httpStatus >= 200 && r.httpStatus < 300, status = accepted ? "accepted" : r.sent === false && r.serviceAccepted === false && Number.isInteger(r.httpStatus) && r.status === "rejected" && [400, 401, 403, 404, 405, 413, 415, 422, 429].includes(r.httpStatus) ? "rejected" : "unknown", safe = { status: accepted ? "service-accepted-no-phone-proof" : status, sent: accepted, serviceAccepted: accepted, httpStatus: Number.isInteger(r.httpStatus) ? r.httpStatus : null, phoneArrivalVerified: false };
      await atomic(db, [fence(db, job, now), q(db, "UPDATE v4c_notifications SET status=?,updated_at=?,result_json=? WHERE job_key=? AND status='claimed'", status, stamp(now), JSON.stringify(safe), job.job_key)]);
      return out(safe);
    }
    if (route === "complete") {
      if (!["VERIFIED", "NOTIFIED"].includes(job.state) || job.report_id !== b.result?.reportId || job.sha256 !== b.result?.sha256) fail("VERIFIED_COMPLETION_REQUIRED", 409);
      await atomic(db, [fence(db, job, now), q(db, "UPDATE v4c_jobs SET status='completed',updated_at=? WHERE job_key=?", stamp(now), job.job_key)]);
      return out({ status: "completed" });
    }
    if (route === "fail") {
      const code = typeof b.diagnostic?.code === "string" && /^[A-Z0-9_]{1,100}$/.test(b.diagnostic.code) ? b.diagnostic.code : "RUNNER_FAILED";
      await atomic(db, [fence(db, job, now), q(db, "UPDATE v4c_jobs SET status='failed',state=CASE WHEN state='NOTIFIED' THEN state ELSE 'FAILED' END,code=?,updated_at=? WHERE job_key=?", code, stamp(now), job.job_key)]);
      return out({ status: "recorded" });
    }
    fail("NOT_FOUND", 404);
  } catch (e) {
    if (e instanceof ApiError) return out({ error: e.message, ...e.extra }, e.status);
    return out({ error: "V4_DATABASE_OR_RUNTIME_FAILED" }, 503);
  }
}
export {
  browserApi,
  runnerApi
};

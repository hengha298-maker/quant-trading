/* Display-only helpers. No storage, network, broker or strategy execution. */
(function (root) {
  "use strict";
  const fail = () => {
    throw new TypeError("数据结构、字段或数值无效");
  };
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const numeric = (value) => {
    if (value === null || value === undefined || value === "") return null;
    if (
      !["number", "string"].includes(typeof value) ||
      (typeof value === "string" && !value.trim())
    )
      fail();
    const number = Number(value);
    if (!Number.isFinite(number) || Math.abs(number) > 1e15) fail();
    return number;
  };
  function money(value) {
    const n = numeric(value);
    return n === null
      ? "未知"
      : new Intl.NumberFormat("zh-CN", {
          style: "currency",
          currency: "CNY",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(n);
  }
  function quantity(value) {
    const n = numeric(value);
    if (n !== null && (!Number.isSafeInteger(n) || n < 0)) fail();
    return n === null
      ? "未知"
      : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(n);
  }
  function price(value) {
    const n = numeric(value);
    // Unit prices/costs are not cash settlement: retain sub-cent input precision.
    return n === null
      ? "未知"
      : new Intl.NumberFormat("zh-CN", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 12,
        }).format(n);
  }
  function sumKnown(values) {
    if (!Array.isArray(values)) fail();
    let sum = 0,
      knownCount = 0;
    for (const value of values) {
      const n = numeric(value);
      if (n !== null) {
        sum += n;
        knownCount += 1;
      }
    }
    // This is a presentation subtotal, never an account balance or execution input.
    return {
      value:
        knownCount || values.length === 0
          ? Math.round((sum + Number.EPSILON) * 100) / 100
          : null,
      complete: knownCount === values.length,
      knownCount,
    };
  }
  function portfolio(envelope) {
    if (
      !envelope ||
      !["demo", "historical"].includes(envelope.mode) ||
      !Array.isArray(envelope.accounts) ||
      envelope.accounts.length === 0
    )
      fail();
    const cash = sumKnown(envelope.accounts.map((a) => a.cash));
    const positions = sumKnown(
      envelope.accounts.flatMap((a) =>
        a.positions.map((p) => {
          const price = numeric(p.referencePrice),
            count = numeric(p.quantity);
          return price === null || count === null ? null : price * count;
        }),
      ),
    );
    const assets = sumKnown([cash.value, positions.value]);
    const coverageComplete =
      envelope.accounts.length > 0 &&
      envelope.accounts.every((a) => a.coverage === "complete");
    return {
      knownAssets: assets.value,
      knownCash: cash.value,
      knownPositionsValue: positions.value,
      cashComplete: cash.complete,
      assetsComplete: cash.complete && positions.complete,
      complete: coverageComplete && cash.complete && positions.complete,
    };
  }
  function validDay(value) {
    if (
      typeof value !== "string" ||
      !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
      value.slice(0, 4) === "0000"
    )
      return false;
    const date = new Date(value + "T00:00:00Z");
    return (
      !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value
    );
  }
  function explicitTimestamp(value) {
    return (
      typeof value === "string" &&
      /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$/.test(
        value,
      ) &&
      validDay(value.slice(0, 10)) &&
      Number.isFinite(Date.parse(value))
    );
  }
  function freshness(timestamp, now = new Date(), maxAgeSeconds = 300) {
    if (timestamp === null || timestamp === undefined || timestamp === "")
      return {
        status: "unknown",
        label: "时间未知",
        ageSeconds: null,
        actionable: false,
      };
    if (validDay(timestamp))
      return {
        status: "unknown",
        label: "未记录日内时刻",
        ageSeconds: null,
        actionable: false,
      };
    if (!explicitTimestamp(timestamp))
      return {
        status: "invalid",
        label: "时间格式无效",
        ageSeconds: null,
        actionable: false,
      };
    const nowValue = now instanceof Date ? now.valueOf() : Date.parse(now);
    if (
      !Number.isFinite(nowValue) ||
      !Number.isFinite(maxAgeSeconds) ||
      maxAgeSeconds < 0
    )
      fail();
    const ageSeconds = (nowValue - Date.parse(timestamp)) / 1000;
    if (ageSeconds < 0)
      return {
        status: "future",
        label: "未来时间，不可用",
        ageSeconds,
        actionable: false,
      };
    if (ageSeconds > maxAgeSeconds)
      return {
        status: "stale",
        label: "历史数据，非实时",
        ageSeconds,
        actionable: false,
      };
    // Freshness alone never qualifies a suggestion or makes fictional data real.
    return {
      status: "fresh",
      label: "时效窗口内，仍需校验",
      ageSeconds,
      actionable: false,
    };
  }
  function time(value) {
    if (value === null || value === undefined || value === "") return "未记录";
    if (validDay(value)) return value + " · 未记录日内时刻";
    if (!explicitTimestamp(value)) return "时间格式无效";
    return (
      new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value)) + "（北京时间）"
    );
  }
  function assertDemo(envelope) {
    if (
      !envelope ||
      envelope.schemaVersion !== 1 ||
      envelope.mode !== "demo" ||
      typeof envelope.datasetId !== "string" ||
      !envelope.datasetId.startsWith("demo-") ||
      envelope.sourceLabel !== "虚构演示数据" ||
      !Array.isArray(envelope.accounts) ||
      !Array.isArray(envelope.strategies) ||
      !Array.isArray(envelope.reports) ||
      envelope.strategies.some((s) => s.actionable !== false) ||
      envelope.reports.some((r) => r.actionable !== false)
    )
      fail();
    return envelope;
  }
  function exactFields(object, fields) {
    if (
      !object ||
      typeof object !== "object" ||
      Array.isArray(object) ||
      Object.keys(object).length !== fields.length ||
      !fields.every((f) => own(object, f))
    )
      fail();
  }
  function p1Number(value, signed = false) {
    if (
      !["number", "string"].includes(typeof value) ||
      (typeof value === "string" && (!value.trim() || value.length > 128))
    )
      fail();
    const text = String(value).trim();
    const match = /^[-+]?(?:\d+(?:\.(\d*))?|\.(\d+))(?:e([-+]?\d+))?$/i.exec(
      text,
    );
    if (!match) fail();
    const exponent =
      Number(match[3] || 0) - (match[1] || match[2] || "").length;
    if (exponent < -12 || exponent > 15) fail();
    const n = numeric(value);
    if (n === null || (!signed && n < 0)) fail();
    return n;
  }
  /**
   * Future display mapping for P1 v1 objects already accepted by its validator.
   * No file/CSV/JSON import exists here; upstream validation, source authenticity,
   * private-path policy and decimal-precision checks remain P1 responsibilities.
   * The defensive structural checks below prevent accidental version/field mixing.
   * This result is historical and assertDemo intentionally rejects it.
   */
  function adaptP1(snapshot, now = new Date()) {
    exactFields(snapshot, [
      "schema_version",
      "as_of",
      "accounts",
      "transaction_history_complete",
    ]);
    const date = now instanceof Date ? now : new Date(now);
    if (!Number.isFinite(date.valueOf())) fail();
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date);
    if (
      snapshot.schema_version !== 1 ||
      !validDay(snapshot.as_of) ||
      snapshot.as_of > today ||
      snapshot.transaction_history_complete !== false ||
      !Array.isArray(snapshot.accounts) ||
      snapshot.accounts.length === 0 ||
      snapshot.accounts.length > 1000
    )
      fail();
    const accountsSeen = new Set(),
      symbolKinds = new Map();
    const accounts = snapshot.accounts.map((a) => {
      exactFields(a, ["account", "broker", "coverage", "cash", "positions"]);
      if (
        typeof a.account !== "string" ||
        !/^[A-Za-z0-9_-]{1,32}$/.test(a.account) ||
        accountsSeen.has(a.account) ||
        typeof a.broker !== "string" ||
        !a.broker ||
        a.broker.length > 128 ||
        /[\x00-\x1f]/.test(a.broker) ||
        !["partial", "complete"].includes(a.coverage) ||
        !Array.isArray(a.positions) ||
        a.positions.length > 10000 ||
        (a.cash === null && a.coverage === "complete")
      )
        fail();
      accountsSeen.add(a.account);
      const cash = a.cash === null ? null : p1Number(a.cash);
      const positionsSeen = new Set();
      const positions = a.positions.map((p) => {
        exactFields(p, [
          "symbol",
          "kind",
          "quantity",
          "available",
          "broker_cost",
          "cost_basis",
        ]);
        if (
          typeof p.symbol !== "string" ||
          !/^\d{6}\.(SH|SZ)$/.test(p.symbol) ||
          positionsSeen.has(p.symbol) ||
          !["stock", "etf"].includes(p.kind) ||
          p.cost_basis !== "broker_display" ||
          !Number.isSafeInteger(p.quantity) ||
          !Number.isSafeInteger(p.available) ||
          p.quantity <= 0 ||
          p.available < 0 ||
          p.available > p.quantity ||
          (symbolKinds.has(p.symbol) && symbolKinds.get(p.symbol) !== p.kind)
        )
          fail();
        positionsSeen.add(p.symbol);
        symbolKinds.set(p.symbol, p.kind);
        return {
          symbol: p.symbol,
          name: p.symbol,
          kind: p.kind,
          quantity: p.quantity,
          available: p.available,
          brokerCost:
            p.broker_cost === null ? null : p1Number(p.broker_cost, true),
          referencePrice: null,
        };
      });
      return {
        id: a.account,
        broker: a.broker,
        coverage: a.coverage,
        cash,
        positions,
      };
    });
    return {
      schemaVersion: 1,
      mode: "historical",
      sourceLabel: "P1 历史快照（需上游验证）",
      holdingsAsOf: snapshot.as_of,
      holdingsTimeKnown: false,
      quoteAsOf: null,
      generatedAt: null,
      syncedAt: null,
      transactionHistoryComplete: false,
      accounts,
      actionable: false,
      displayOnly: true,
    };
  }
  const api = Object.freeze({
    money,
    price,
    quantity,
    sumKnown,
    portfolio,
    freshness,
    time,
    assertDemo,
    adaptP1,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.GooseModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

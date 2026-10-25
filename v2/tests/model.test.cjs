"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const model = require("../model.js");
const demo = require("../demo-data.js");
const clone = (value) => JSON.parse(JSON.stringify(value));
function p1Fixture() {
  return {
    schema_version: 1,
    as_of: "2026-10-19",
    transaction_history_complete: false,
    accounts: [
      {
        account: "DEMO_HB",
        broker: "华宝虚构",
        coverage: "partial",
        cash: null,
        positions: [
          {
            symbol: "600900.SH",
            kind: "stock",
            quantity: 300,
            available: 200,
            broker_cost: "-1.23",
            cost_basis: "broker_display",
          },
        ],
      },
    ],
  };
}
test("unknown numeric values remain unknown, while genuine zero is displayed as zero", () => {
  for (const value of [null, undefined, ""]) {
    assert.equal(model.money(value), "未知");
    assert.equal(model.quantity(value), "未知");
  }
  assert.match(model.money(0), /0\.00/);
  assert.equal(model.quantity(0), "0");
  assert.match(model.money(-1.23), /1\.23/);
  for (const value of [NaN, Infinity, false, {}, "not-a-number"])
    assert.throws(() => model.money(value));
});
test("unknown-only subtotals never produce a zero balance", () => {
  assert.deepEqual(model.sumKnown([null, null]), {
    value: null,
    complete: false,
    knownCount: 0,
  });
  assert.deepEqual(model.sumKnown([10.1, null, "0.2"]), {
    value: 10.3,
    complete: false,
    knownCount: 2,
  });
  assert.deepEqual(model.sumKnown([]), {
    value: 0,
    complete: true,
    knownCount: 0,
  });
});
test("unit costs retain sub-cent precision independently of cash formatting", () => {
  assert.equal(model.price(1.046), "1.046");
  assert.equal(model.price("1.028"), "1.028");
  assert.equal(model.price("-0.1234"), "-0.1234");
  assert.equal(model.price(null), "未知");
  assert.match(model.money(1.046), /1\.05/);
});
test("portfolio reports a known subtotal without declaring partial accounts complete", () => {
  const summary = model.portfolio(demo);
  assert.equal(summary.knownCash, 12500);
  assert.equal(summary.knownPositionsValue, 11131.6);
  assert.equal(summary.knownAssets, 23631.6);
  assert.equal(summary.complete, false);
  assert.equal(summary.cashComplete, false);
  const missing = clone(demo);
  missing.accounts.forEach((a) => {
    a.cash = null;
    a.positions.forEach((p) => {
      p.referencePrice = null;
    });
  });
  assert.equal(model.portfolio(missing).knownAssets, null);
});
test("a complete empty-cash account is not mistaken for missing data", () => {
  const envelope = {
    mode: "historical",
    accounts: [{ coverage: "complete", cash: 0, positions: [] }],
  };
  const result = model.portfolio(envelope);
  assert.equal(result.knownCash, 0);
  assert.equal(result.knownAssets, 0);
  assert.equal(result.knownPositionsValue, 0);
  assert.equal(result.complete, true);
});
test("the demo is immutable and both independent strategies remain non-actionable", () => {
  assert.equal(model.assertDemo(demo), demo);
  assert.equal(demo.strategies.length, 2);
  assert.deepEqual(
    demo.strategies.map((s) => s.id),
    ["long-term", "intraday"],
  );
  assert.ok(
    demo.strategies.every(
      (s) => s.actionable === false && s.validation && s.limitations.length,
    ),
  );
  assert.ok(demo.reports.every((r) => r.actionable === false));
  assert.throws(() => {
    demo.accounts[0].cash = 0;
  });
});
test("a page cannot accept historical or paper accounts as the demo dataset", () => {
  for (const mode of ["historical", "paper", "live"])
    assert.throws(() => model.assertDemo({ ...demo, mode }));
  assert.throws(() => model.portfolio({ ...demo, mode: "paper" }));
  assert.throws(() => model.assertDemo({ ...demo, datasetId: "real-data" }));
  const changed = clone(demo);
  changed.strategies[0].actionable = true;
  assert.throws(() => model.assertDemo(changed));
});
test("date-only, invalid, future, stale and fresh times never become executable advice", () => {
  const now = "2026-10-21T10:00:00+08:00";
  assert.equal(model.freshness(null, now).status, "unknown");
  assert.equal(model.freshness("2026-10-19", now).status, "unknown");
  assert.equal(
    model.freshness("2026-02-30T10:00:00+08:00", now).status,
    "invalid",
  );
  assert.equal(model.freshness("2026-10-21T10:00:00", now).status, "invalid");
  assert.equal(
    model.freshness("2026-10-21T10:01:00+08:00", now).status,
    "future",
  );
  assert.equal(model.freshness(demo.quoteAsOf, now).status, "stale");
  assert.equal(
    model.freshness("2026-10-21T09:59:00+08:00", now).status,
    "fresh",
  );
  assert.equal(
    model.freshness("2026-10-21T09:59:00+08:00", now).actionable,
    false,
  );
});
test("formatting does not invent daily snapshot times or cloud synchronization", () => {
  assert.match(model.time("2026-10-19"), /未记录日内时刻/);
  assert.equal(model.time(null), "未记录");
  assert.match(model.time("2026-10-19T07:00:00Z"), /15:00/);
});
test("P1 display mapping retains unknown data, negative broker cost and date-only history", () => {
  const source = p1Fixture();
  const original = clone(source);
  const output = model.adaptP1(source);
  assert.deepEqual(source, original);
  assert.equal(output.mode, "historical");
  assert.equal(output.holdingsAsOf, "2026-10-19");
  assert.equal(output.holdingsTimeKnown, false);
  assert.equal(output.generatedAt, null);
  assert.equal(output.accounts[0].cash, null);
  assert.equal(output.accounts[0].positions[0].referencePrice, null);
  assert.equal(output.accounts[0].positions[0].brokerCost, -1.23);
  assert.equal(output.accounts[0].positions[0].available, 200);
  assert.equal(output.actionable, false);
  assert.equal(output.transactionHistoryComplete, false);
  assert.throws(() => model.assertDemo(output));
});
test("P1 mapping rejects version mixing, added fields and account or security duplicates", () => {
  for (const mutate of [
    (p) => {
      p.schema_version = 999;
    },
    (p) => {
      p.currentTime = "invented";
    },
    (p) => {
      p.accounts[0].secret = "never-read";
    },
    (p) => {
      p.accounts.push(clone(p.accounts[0]));
    },
    (p) => {
      p.accounts[0].positions.push(clone(p.accounts[0].positions[0]));
    },
    (p) => {
      p.accounts[0].positions[0].available = 301;
    },
    (p) => {
      p.accounts[0].positions[0].quantity = true;
    },
    (p) => {
      p.accounts[0].cash = "";
    },
    (p) => {
      p.accounts[0].cash = "NaN";
    },
    (p) => {
      p.accounts[0].positions[0].broker_cost = "";
    },
    (p) => {
      p.accounts[0].coverage = "complete";
    },
    (p) => {
      p.transaction_history_complete = true;
    },
    (p) => {
      p.as_of = "2026-02-30";
    },
    (p) => {
      p.as_of = "2099-01-01";
    },
    (p) => {
      p.accounts[0].cash = "0x10";
    },
    (p) => {
      p.accounts[0].cash = "0.0000000000001";
    },
  ]) {
    const value = p1Fixture();
    mutate(value);
    assert.throws(() => model.adaptP1(value));
  }
});

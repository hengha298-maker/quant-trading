"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function worker(scope = "https://example.invalid/v2/") {
  const listeners = {};
  const deleted = [];
  const added = [];
  const matched = [];
  let fetched = 0;
  const cache = {
    addAll: async (urls) => added.push(...urls),
    match: async (key) => {
      matched.push(key);
      return { ok: true, cached: true };
    },
    put: async () => {},
  };
  const prefix = `goose-v2-demo:${encodeURIComponent(scope)}:`;
  const context = {
    URL,
    Set,
    Promise,
    self: {
      registration: { scope },
      addEventListener: (type, callback) => {
        listeners[type] = callback;
      },
      skipWaiting: async () => {},
      clients: { claim: async () => {} },
    },
    caches: {
      open: async () => cache,
      keys: async () => [
        `${prefix}old`,
        `${prefix}20261021-v3`,
        "other-application",
        "goose-v2-demo:another-scope:old",
      ],
      delete: async (key) => deleted.push(key),
    },
    fetch: async () => {
      fetched += 1;
      return { ok: true, type: "basic", clone: () => ({}) };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../sw.js"), "utf8"),
    context,
  );
  return { listeners, added, deleted, matched, fetched: () => fetched, prefix };
}

test("SW installs only bundled demo resources within its own scope", async () => {
  const w = worker();
  let promise;
  w.listeners.install({
    waitUntil: (p) => {
      promise = p;
    },
  });
  await promise;
  assert.ok(w.added.includes("https://example.invalid/v2/demo-data.js"));
  assert.ok(
    w.added.every((url) => url.startsWith("https://example.invalid/v2/")),
  );
  assert.ok(
    w.added.every((url) => !url.includes("/api/") && !url.endsWith(".json")),
  );
});

test("SW activation deletes only old caches belonging to this exact app scope", async () => {
  const w = worker();
  let promise;
  w.listeners.activate({
    waitUntil: (p) => {
      promise = p;
    },
  });
  await promise;
  assert.deepEqual(w.deleted, [`${w.prefix}old`]);
});

test("SW does not intercept APIs, private reports, query URLs, writes, or other origins", () => {
  const w = worker();
  const cases = [
    ["GET", "https://example.invalid/v2/api/holdings"],
    ["GET", "https://example.invalid/v2/api/holdings#/"],
    ["GET", "https://example.invalid/v2/reports/private.json"],
    ["GET", "https://example.invalid/v2/demo-data.js?account=private"],
    ["GET", "https://example.invalid/v2/?account=private#/"],
    ["POST", "https://example.invalid/v2/"],
    ["GET", "https://other.invalid/v2/demo-data.js"],
    ["GET", "https://example.invalid/another-app/index.html"],
  ];
  for (const [method, url] of cases) {
    let intercepted = false;
    w.listeners.fetch({
      request: { method, url },
      respondWith: () => {
        intercepted = true;
      },
    });
    assert.equal(intercepted, false, `${method} ${url}`);
  }
});

test("SW can serve the public app shell from cache without the network", async () => {
  const w = worker();
  let response;
  w.listeners.fetch({
    request: { method: "GET", url: "https://example.invalid/v2/index.html" },
    respondWith: (p) => {
      response = p;
    },
  });
  assert.equal((await response).cached, true);
  assert.equal(w.fetched(), 0);
});

test("SW canonicalizes hash navigation to the cached document for offline reloads", async () => {
  for (const [requestURL, documentURL] of [
    ["https://example.invalid/v2/#/", "https://example.invalid/v2/"],
    ["https://example.invalid/v2/#/holdings", "https://example.invalid/v2/"],
    [
      "https://example.invalid/v2/index.html#/reports/demo-1",
      "https://example.invalid/v2/index.html",
    ],
  ]) {
    const w = worker();
    let response;
    w.listeners.fetch({
      request: { method: "GET", mode: "navigate", url: requestURL },
      respondWith: (p) => {
        response = p;
      },
    });
    assert.equal((await response).cached, true);
    assert.deepEqual(w.matched, [documentURL]);
    assert.equal(w.fetched(), 0);
  }
});

/* Optional browser QA. Requires Node.js and Playwright (development only).
 * Start `python v2/preview.py --no-open`, then run `node v2/tests/browser.cjs`.
 * No browser tooling is required to use the prototype.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

const baseURL = process.env.V2_BASE_URL || "http://127.0.0.1:8765";
const output =
  process.env.V2_SCREENSHOT_DIR ||
  path.resolve(__dirname, "../../work/screenshots");
const executablePath = process.env.V2_CHROMIUM || undefined;
const routes = [
  ["home", "#/"],
  ["holdings", "#/holdings"],
  ["strategies", "#/strategies"],
  ["reports", "#/reports"],
];
const results = [];
const errors = [];
const securityErrors = [];
const renderingErrors = [];
const consoleErrors = [];
const externalRequests = [];
let browser;

async function check(name, task) {
  try {
    await task();
    results.push({ name, result: "passed" });
    process.stdout.write(`PASS ${name}\n`);
  } catch (error) {
    if (error.environmentSkip) {
      results.push({ name, result: "skipped", reason: error.message });
      process.stdout.write(`SKIP ${name}: ${error.message}\n`);
      return;
    }
    results.push({ name, result: "failed", error: error.message });
    process.stdout.write(`FAIL ${name}: ${error.message}\n`);
  }
}
async function navigate(page, route) {
  await page.goto(`${baseURL}/${route}`);
  await page.locator("main").waitFor();
  await page.waitForTimeout(80);
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(400);
}
async function clickRoute(page, route) {
  await page.locator(`a[href="${route}"]:visible`).first().click();
  await page.waitForFunction((expected) => location.hash === expected, route);
}
async function assertDemo(page) {
  await page.getByTestId("demo-banner").waitFor({ state: "visible" });
  assert.match(await page.getByTestId("demo-banner").innerText(), /演示/);
}
async function assertNoOverflow(page) {
  const size = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
  }));
  assert.ok(
    size.body <= size.viewport + 1 && size.document <= size.viewport + 1,
    `horizontal overflow: ${JSON.stringify(size)}`,
  );
}
async function closeDialog(page) {
  const dialog = page.getByRole("dialog");
  await dialog.waitFor({ state: "visible" });
  await dialog.getByRole("button", { name: /关闭/ }).first().click();
  await dialog.waitFor({ state: "hidden" });
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
    locale: "zh-CN",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.setDefaultNavigationTimeout(12000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    consoleErrors.push(message.text());
    if (/Refused to|Content Security Policy/i.test(message.text()))
      securityErrors.push(message.text());
    if (/Expected number|attribute d:|Uncaught/i.test(message.text()))
      renderingErrors.push(message.text());
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.protocol.startsWith("http") &&
      url.origin !== new URL(baseURL).origin
    )
      externalRequests.push(request.url());
  });

  await check("four interactive pages / iPhone screenshots", async () => {
    for (const [name, route] of routes) {
      await navigate(page, route);
      await assertDemo(page);
      await assertNoOverflow(page);
      await page.screenshot({
        path: path.join(output, `iphone-${name}-viewport.png`),
        fullPage: false,
      });
      await page.screenshot({
        path: path.join(output, `iphone-${name}.png`),
        fullPage: true,
      });
    }
  });

  await check(
    "all pages fit 375 / 390 / 430 / 1440 pixel viewports",
    async () => {
      for (const width of [375, 390, 430, 1440]) {
        await page.setViewportSize({
          width,
          height: width === 1440 ? 1000 : 844,
        });
        for (const [, route] of routes) {
          await navigate(page, route);
          await assertNoOverflow(page);
        }
      }
      for (const [name, route] of routes) {
        await navigate(page, route);
        await page.screenshot({
          path: path.join(output, `desktop-${name}-viewport.png`),
          fullPage: false,
        });
        await page.screenshot({
          path: path.join(output, `desktop-${name}.png`),
          fullPage: true,
        });
      }
      await page.setViewportSize({ width: 390, height: 844 });
    },
  );

  await check("navigation, strategy detail and return", async () => {
    await navigate(page, "#/");
    for (const route of ["#/holdings", "#/strategies", "#/reports", "#/"])
      await clickRoute(page, route);
    await clickRoute(page, "#/strategies");
    for (const id of ["long-term", "intraday"]) {
      await clickRoute(page, `#/strategies/${id}`);
      assert.match(await page.locator("main").innerText(), /研究|验证/);
      await page.getByRole("link", { name: /返回/ }).first().click();
      await page.waitForFunction(() => location.hash === "#/strategies");
    }
  });

  await check("report list filter, detail and return", async () => {
    await navigate(page, "#/reports");
    await page.locator('[data-report-filter="intraday"]').click();
    assert.equal(
      await page
        .locator('[data-report-filter="intraday"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.ok((await page.locator(".report-row").count()) > 0);
    assert.match(await page.locator(".report-list").innerText(), /做T/);
    const entries = page.locator('a[href^="#/reports/"]:visible');
    assert.ok((await entries.count()) > 0, "intraday report is available");
    await entries.first().click();
    await page.waitForFunction(() => location.hash.startsWith("#/reports/"));
    assert.match(await page.locator("main").innerText(), /数据|行情/);
    await assertDemo(page);
    await assertNoOverflow(page);
    await page.screenshot({
      path: path.join(output, "iphone-report-detail.png"),
      fullPage: true,
    });
    await page.getByRole("link", { name: /返回/ }).first().click();
    await page.waitForFunction(() => location.hash === "#/reports");
    await page.locator('[data-report-filter="all"]').click();
  });

  await check(
    "account filter preserves separate Huabao / Yinhe accounts",
    async () => {
      await navigate(page, "#/holdings");
      for (const id of ["HB", "YH", "all"]) {
        await page.locator(`[data-account="${id}"]`).click();
        assert.equal(
          await page
            .locator(`[data-account="${id}"]`)
            .getAttribute("aria-pressed"),
          "true",
        );
        assert.equal(
          await page.locator(".account-card").count(),
          id === "all" ? 2 : 1,
        );
        if (id !== "all")
          assert.match(
            await page.locator(".account-card").innerText(),
            id === "HB" ? /华宝/ : /银河/,
          );
        if (id === "YH")
          assert.match(
            await page.locator(".account-card").innerText(),
            /1\.046/,
            "ETF per-share broker cost preserves three decimal places",
          );
        await assertNoOverflow(page);
      }
      assert.match(await page.locator("main").innerText(), /华宝/);
      assert.match(await page.locator("main").innerText(), /银河/);
    },
  );

  await check("holding fields expand and holding detail returns", async () => {
    await navigate(page, "#/holdings");
    await page.locator('[data-account="all"]').click();
    const details = page.locator(".account-foot details").first();
    await details.locator("summary").click();
    assert.equal(await details.getAttribute("open"), "");
    await page.locator(".position-link").first().click();
    await page.waitForFunction(() => location.hash.startsWith("#/holdings/"));
    assert.match(await page.locator("main").innerText(), /历史|快照|可卖/);
    await page.getByRole("link", { name: /返回/ }).first().click();
    await page.waitForFunction(() => location.hash === "#/holdings");
  });

  await check("hide / reveal demo amounts", async () => {
    await navigate(page, "#/");
    const before = await page.getByTestId("asset-total").innerText();
    await page.getByTestId("toggle-amounts").click();
    const hidden = await page.getByTestId("asset-total").innerText();
    assert.notEqual(hidden, before);
    assert.match(hidden, /[•*＊]/);
    await page.getByTestId("toggle-amounts").click();
    assert.equal(await page.getByTestId("asset-total").innerText(), before);
  });

  await check("data status and future integration placeholders", async () => {
    await navigate(page, "#/");
    await page.getByTestId("data-status").first().click();
    assert.match(await page.getByRole("dialog").innerText(), /演示|来源/);
    await closeDialog(page);
    await page.getByTestId("future-settings").first().click();
    const text = await page.getByRole("dialog").innerText();
    for (const item of [/预算/, /成交/, /Bark/, /同步/])
      assert.match(text, item);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: /添加到主屏幕/ })
      .click();
    assert.match(await page.getByRole("dialog").innerText(), /HTTPS/);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: /返回功能预留/ })
      .click();
    await closeDialog(page);
  });

  await check("unknown routes show recoverable empty states", async () => {
    for (const route of [
      "#/not-a-route",
      "#/reports/not-a-report",
      "#/holdings/unknown/600900.SH",
      "#/strategies/not-a-strategy",
    ]) {
      await navigate(page, route);
      assert.match(await page.locator("main").innerText(), /不存在/);
      await assertDemo(page);
      await assertNoOverflow(page);
      await page.getByRole("link", { name: /返回/ }).first().click();
      assert.ok(!page.url().includes("not-a-"));
    }
  });

  await check(
    "skip link focuses content without changing application route",
    async () => {
      await navigate(page, "#/holdings");
      const route = new URL(page.url()).hash;
      await page.locator(".skip-link").focus();
      await page.keyboard.press("Enter");
      assert.equal(new URL(page.url()).hash, route);
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "main",
      );
    },
  );

  await check("PWA manifest and local icons", async () => {
    const response = await context.request.get(
      `${baseURL}/manifest.webmanifest`,
    );
    assert.equal(response.status(), 200);
    const manifest = await response.json();
    assert.match(manifest.name, /养鹅/);
    assert.equal(manifest.display, "standalone");
    assert.ok(manifest.icons.some((icon) => icon.sizes === "192x192"));
    assert.ok(manifest.icons.some((icon) => icon.sizes === "512x512"));
    for (const icon of manifest.icons) {
      const result = await context.request.get(
        new URL(icon.src, `${baseURL}/`).href,
      );
      assert.equal(result.status(), 200, icon.src);
    }
  });

  await check(
    "localhost service worker supports offline demo navigation",
    async () => {
      await navigate(page, "#/");
      await page.waitForFunction(() => "serviceWorker" in navigator);
      await page.evaluate(async () => {
        await Promise.race([
          navigator.serviceWorker.ready,
          new Promise((_, reject) =>
            setTimeout(
              () => reject(new Error("service worker did not become ready")),
              12000,
            ),
          ),
        ]);
      });
      await page.reload();
      await page.waitForFunction(() =>
        Boolean(navigator.serviceWorker.controller),
      );
      await context.setOffline(true);
      try {
        await page.reload();
        await assertDemo(page);
        await clickRoute(page, "#/holdings");
        assert.match(await page.locator("main").innerText(), /华宝|银河/);
      } finally {
        await context.setOffline(false);
      }
    },
  );

  await check(
    "direct file preview remains interactive without server",
    async () => {
      const local = await context.newPage();
      local.on("pageerror", (error) =>
        errors.push(`file preview: ${error.message}`),
      );
      try {
        await local.goto(
          pathToFileURL(path.resolve(__dirname, "../index.html")).href,
        );
      } catch (error) {
        await local.close();
        if (error.message.includes("ERR_BLOCKED_BY_ADMINISTRATOR")) {
          const skip = new Error(
            "Managed browser policy blocks file://; use verified localhost preview. Direct-file operation was not verified.",
          );
          skip.environmentSkip = true;
          throw skip;
        }
        throw error;
      }
      await assertDemo(local);
      await clickRoute(local, "#/holdings");
      assert.match(await local.locator("main").innerText(), /华宝|银河/);
      await clickRoute(local, "#/reports");
      await local.close();
    },
  );

  await check(
    "no unexpected browser exceptions or external data requests",
    async () => {
      assert.deepEqual(errors, []);
      assert.deepEqual(externalRequests, []);
      assert.deepEqual(securityErrors, []);
      assert.deepEqual(renderingErrors, []);
      assert.deepEqual(consoleErrors, []);
    },
  );
  await context.close();
})()
  .catch((error) => {
    results.push({
      name: "browser setup",
      result: "failed",
      error: error.message,
    });
    process.stderr.write(`${error.stack}\n`);
  })
  .finally(async () => {
    if (browser) await browser.close();
    await fs.mkdir(output, { recursive: true });
    const summary = {
      passed: results.filter((item) => item.result === "passed").length,
      failed: results.filter((item) => item.result === "failed").length,
      skipped: results.filter((item) => item.result === "skipped").length,
      results,
      browserErrors: errors,
      securityErrors,
      renderingErrors,
      consoleErrors,
      externalRequests,
    };
    await fs.writeFile(
      path.join(output, "browser-results.json"),
      JSON.stringify(summary, null, 2),
    );
    process.stdout.write(
      `${summary.passed} passed, ${summary.failed} failed, ${summary.skipped} skipped\n`,
    );
    process.exitCode = summary.failed ? 1 : 0;
  });

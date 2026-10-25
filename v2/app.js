/* Read-only, offline-first DEMO UI. No account import, persistence or external API. */
(function () {
  "use strict";
  const M = window.GooseModel;
  const main = document.getElementById("main");
  let data;
  try {
    data = M.assertDemo(window.GooseDemo);
  } catch (_) {
    main.innerHTML =
      '<p class="fatal">无法加载演示数据。请重新解压完整原型；未生成任何建议。</p>';
    return;
  }
  const state = { hidden: false, account: "all", reportFilter: "all" };
  const e = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const icons = {
    home: "M3 10.5 12 3l9 7.5M5 9.5V21h5v-7h4v7h5V9.5",
    wallet:
      "M4 6h14a2 2 0 0 1 2 2v11H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12v2M20 11h-6v5h6M16 13.5h.01",
    leaf: "M20 3c1 10-4 16-10 16-5 0-8-5-5-9 3-4 9-3 15-7ZM5 21l11-11M9 17v-5M13 13h5",
    report: "M6 3h9l4 4v14H5V3h1ZM14 3v5h5M8 12h8M8 16h6",
    settings:
      "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3ZM15 12a3 3 0 1 0-6 0 3 3 0 0 0 6 0",
    eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7ZM15 12a3 3 0 1 0-6 0 3 3 0 0 0 6 0",
    eyeOff:
      "m3 3 18 18M10 5c7-1 12 7 12 7s-1 2-3 4M6 6c-3 2-4 6-4 6s4 7 10 7c2 0 4-1 5-2M10 10a3 3 0 0 0 4 4",
    info: "M12 8h.01M12 11v5M22 12a10 10 0 1 0-20 0 10 10 0 0 0 20 0",
    trend: "m3 17 6-6 4 4 8-10M15 5h6v6M3 21h18",
    waves: "m2 8 4 4 4-7 4 14 4-9 4 3",
    budget: "M4 5h16v15H4V5ZM8 3v4M16 3v4M4 10h16M8 14h2M14 14h2",
    check: "m5 12 4 4L19 6M21 12v8H3V3h13",
    bell: "M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9ZM10 21h4",
    cloud:
      "M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5.5 5.5 0 0 1 0 11M12 12v9m-3-3 3 3 3-3",
    lock: "M6 10h12v11H6V10ZM8 10V6a4 4 0 0 1 8 0v4M12 14v3",
    arrow: "M5 12h14m-5-5 5 5-5 5",
    clock: "M12 7v5l3 2M22 12a10 10 0 1 0-20 0 10 10 0 0 0 20 0",
  };
  function icon(name) {
    return `<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name] || icons.info}"/></svg>`;
  }
  const pill = (label, style = "") =>
    `<span class="pill ${style}">${e(label)}</span>`;
  const arrow = '<span class="arrow" aria-hidden="true">↗</span>';
  const amount = (value) => (state.hidden ? "••••" : e(M.money(value)));
  const price = (value) => (state.hidden ? "••••" : e(M.price(value)));
  const units = (value) => (state.hidden ? "••••" : e(M.quantity(value)));
  const list = (values) =>
    `<ul class="clean-list">${values.map((x) => `<li>${e(x)}</li>`).join("")}</ul>`;
  const featureKeys = ["monthlyBudget", "tradeConfirmation", "bark", "sync"];
  const featureIcons = {
    monthlyBudget: "budget",
    tradeConfirmation: "check",
    bark: "bell",
    sync: "cloud",
  };
  const navItems = [
    ["/", "home", "首页"],
    ["/holdings", "wallet", "持仓"],
    ["/strategies", "leaf", "策略"],
    ["/reports", "report", "报告"],
  ];
  document.querySelector(".demo-strip").dataset.testid = "demo-banner";
  document.getElementById("data-status-button").dataset.testid = "data-status";
  document.querySelector('[data-dialog="settings"]').dataset.testid =
    "future-settings";
  document.getElementById("settings-icon").innerHTML = icon("settings");

  function pageHead(kicker, title, description) {
    return `<div class="page-head"><div><div class="eyebrow">${e(kicker)}</div><h1>${e(title)}</h1><p>${e(description)}</p></div><div class="head-note">固定演示样本<span>2026.10.19 · 非实时</span></div></div>`;
  }
  function dataLines(report = data) {
    return `<dl class="data-lines"><div class="data-line"><dt>参考行情截至</dt><dd>${e(M.time(report.quoteAsOf))}</dd></div><div class="data-line"><dt>持仓快照日期</dt><dd>${e(report.holdingsAsOf || "未知")} · 时刻未知</dd></div><div class="data-line"><dt>报告生成</dt><dd>${e(M.time(report.generatedAt))}</dd></div><div class="data-line"><dt>云端最后同步</dt><dd>${report.syncedAt ? e(M.time(report.syncedAt)) : "未接入"}</dd></div></dl>`;
  }
  function featureGrid() {
    return `<div class="feature-grid">${featureKeys.map((key) => `<button class="feature-button" data-dialog="${key}">${icon(featureIcons[key])}<span><strong>${e(data.features[key].label)}</strong><small>功能预留 · 尚未启用</small></span></button>`).join("")}</div>`;
  }
  function stockIdentity(position) {
    return `<div class="stock-id"><span class="stock-symbol ${position.kind === "etf" ? "blue" : ""}">${position.kind === "etf" ? "ETF" : "股"}</span><div><h3>${e(position.name)}</h3><small>${e(position.symbol)}</small></div></div>`;
  }
  function unavailable(
    text = "缺少真实行情、已核验费用和当前持仓，本期不提供操作价格、数量或资金需求。",
  ) {
    return `<div class="unavailable-box"><strong>${icon("info")} 无法形成可靠建议</strong><p>${e(text)}</p></div>`;
  }

  function home() {
    const portfolio = M.portfolio(data);
    return `${pageHead("慢慢积累 · 认真研究", "让每一份积累，都有方向。", "长期投资与日内观察，在这里各有章法。")}
      <div class="home-grid"><div class="main-column">
        <section class="card asset-hero" aria-label="虚构演示资产概况"><div class="card-top"><div class="asset-title">已知资产 · 虚构演示<button class="icon-button" data-action="privacy" data-testid="toggle-amounts" aria-label="${state.hidden ? "显示" : "隐藏"}金额" aria-pressed="${state.hidden}">${icon(state.hidden ? "eyeOff" : "eye")}</button></div><span class="hero-chip">部分数据</span></div>
          <div class="asset-total" data-testid="asset-total">${state.hidden ? "••••••" : `<span class="currency">¥</span>${e(M.money(portfolio.knownAssets).replace(/^[¥￥]/, ""))}`}</div><p class="asset-context">仅合计已知部分，银河现金未知 · 固定样例参考价估算</p>
          <div class="hero-metrics"><dl class="hero-metric"><dt>已知可用现金</dt><dd>${amount(portfolio.knownCash)}</dd></dl><dl class="hero-metric"><dt>持仓参考市值 · 演示</dt><dd>${amount(portfolio.knownPositionsValue)}</dd></dl></div>
        </section>
        <section><div class="section-heading"><h2>我的重点关注</h2><a class="text-link" href="#/holdings">查看持仓 ${icon("arrow")}</a></div><div class="watch-list">
          ${data.focusAssets.map((asset, i) => `<a class="card watch-card" href="#/strategies/${i ? "intraday" : "long-term"}">${`<div class="watch-top">${stockIdentity({ ...asset, kind: i ? "etf" : "stock" })}${pill(asset.statusLabel, i ? "blue" : "")}</div>`}<div class="watch-bottom"><p class="watch-copy"><strong>${i ? "先看底仓与成本，再看机会" : "把分红与现金流，放进长期视角"}</strong>${i ? "分钟样本未接入 · 暂无交易信号" : "财务与估值待核验 · 暂无加仓建议"}</p>${arrow}</div></a>`).join("")}
        </div></section>
        <section class="card"><div class="card-top"><h2>今日市场</h2>${pill("数据未接入", "gray")}</div><div class="market-placeholder">${icon("trend")}<div><strong>市场可以等待，数据不能猜测。</strong><p>指数趋势、成交量与行业观察将在后续接入。</p></div></div><div class="market-meta"><span>不展示虚构涨跌幅</span><span>真实来源接入后更新</span></div></section>
      </div><div class="side-column">
        <section class="card research-card"><div class="card-top"><span class="section-label">最新研究摘要</span>${pill("演示记录", "gray")}</div><span class="research-accent"></span><h3>先把数据看清楚，<br>再作决定。</h3><p>持仓时刻与费用还未核验。没有可靠依据时，保留观察也是研究的一部分。</p><a class="text-link" href="#/reports/${e(data.reports[0].id)}">阅读完整记录 ${icon("arrow")}</a></section>
        <section class="card"><div class="card-top"><h2>数据有迹可循</h2><button class="text-link" data-dialog="status">详情 ↗</button></div>${dataLines()}<p class="tiny-note">全部来自本地虚构演示夹具。刷新页面不会更新行情，也不会产生建议。</p></section>
        <section class="card"><div class="card-top"><h2>留意这两件事</h2>${icon("bell")}</div><div class="reminder-row"><span class="dot"></span><p>银河示例账户的现金尚未知<span>已知资产不等于完整账户资产</span></p></div><div class="reminder-row"><span class="dot"></span><p>持仓仅记录日期，缺少日内时刻<span>可卖数量只作历史展示</span></p></div></section>
      </div></div>`;
  }
  function accountCard(account) {
    return `<section class="card account-card" data-account-card="${e(account.id)}"><div class="account-header"><div class="account-title-row"><div class="account-title"><span class="broker-icon ${account.id.includes("YH") ? "yinhe" : ""}">${account.id.includes("YH") ? "银" : "华"}</span><div><h2>${e(account.broker)}</h2><small>${e(account.id)} · 虚构账户</small></div></div>${pill(account.coverage === "complete" ? "声明已录全" : "部分录入", account.coverage === "complete" ? "" : "amber")}</div><div class="account-cash"><span>可用现金 · 演示</span><strong>${amount(account.cash)}</strong></div></div><div class="positions">${account.positions.map((p) => `<a class="position-link" href="#/holdings/${e(account.id)}/${e(p.symbol)}">${stockIdentity(p)}<dl class="position-metrics"><div class="metric"><dt>持有 / ${p.kind === "etf" ? "份" : "股"}</dt><dd>${units(p.quantity)}</dd></div><div class="metric"><dt>历史可卖数量</dt><dd>${units(p.available)}</dd></div><div class="metric"><dt>每股 / 份成本 · 元</dt><dd>${price(p.brokerCost)}</dd></div></dl></a>`).join("")}</div><div class="account-foot"><details><summary>快照 ${e(data.holdingsAsOf)} · 日内时刻未知</summary><p>完整性只是虚构录入声明，尚未核验；券商成本不是累计收益。点击持仓可查看字段说明。</p></details></div></section>`;
  }
  function holdings() {
    const shown = data.accounts.filter(
      (a) => state.account === "all" || a.id === `DEMO_${state.account}`,
    );
    return `${pageHead("账户各自清楚 · 判断才有依据", "我的持仓", "两家券商分开查看，未知信息保留未知。")}<div class="subtle-banner">${icon("info")}<span>虚构历史快照 · 可卖数量不代表当前可卖额度，所有参考价格均非实时。</span></div>
      <div class="toolbar"><div class="segmented" role="group" aria-label="账户筛选">${[
        ["all", "全部账户"],
        ["HB", "华宝"],
        ["YH", "银河"],
      ]
        .map(
          ([id, label]) =>
            `<button class="segment" data-account="${id}" aria-pressed="${state.account === id}">${label}</button>`,
        )
        .join(
          "",
        )}</div><span class="toolbar-label">人民币 · 演示</span></div><div class="account-grid">${shown.map(accountCard).join("")}</div>
      <section class="section-spaced"><div class="section-heading"><h2>为下一步留好位置</h2><span class="muted"><small>本期只读</small></span></div>${featureGrid()}</section>`;
  }
  function strategies() {
    return `${pageHead("两条路径 · 同样认真", "研究与策略", "长期积累与日内做T，独立研究、分别验证。")}<div class="strategy-grid">${data.strategies.map((s, i) => `<section class="card strategy-card"><div class="strategy-visual ${i ? "t-visual" : ""}"><span class="large-icon">${icon(i ? "waves" : "leaf")}</span><span class="strategy-number">0${i + 1}</span></div><span class="section-label">${i ? "底仓与成本，先于机会" : "耐心与计划，一起生长"}</span><h2>${e(s.title)}</h2><p>${e(s.summary)}</p>${pill(i ? "样本不足 · 无交易信号" : "研究中 · 暂不形成建议", i ? "blue" : "")}<div class="strategy-scope">关注 ${e(s.symbols.join(" / "))}</div><a class="primary-link" href="#/strategies/${e(s.id)}">查看研究依据 ${icon("arrow")}</a></section>`).join("")}</div><section class="card note-card"><h3>独立研究，共享账户约束</h3><p>两套策略不能重复使用同一笔现金或可卖底仓。未来接入真实数据后，将分别检查预算、费用、T+1和验证状态；本期没有任何买卖信号。</p><details class="detail-disclosure"><summary>为什么目前不提供操作数量？</summary><p>虚构数据不能支撑真实建议。尚缺真实持仓时刻、财务与分钟样本、已核验费用、交易规则及样本外验证。</p></details></section>`;
  }
  function reports() {
    const filtered = data.reports.filter(
      (r) => state.reportFilter === "all" || r.type === state.reportFilter,
    );
    return `${pageHead("每一次判断 · 都留有记录", "研究报告", "回看当时的依据，也看清当时的数据限制。")}<div class="toolbar"><div class="segmented" role="group" aria-label="报告筛选">${[
      ["all", "全部"],
      ["long-term", "长期"],
      ["intraday", "做T"],
      ["data", "数据"],
    ]
      .map(
        ([id, label]) =>
          `<button class="segment" data-report-filter="${id}" aria-pressed="${state.reportFilter === id}">${label}</button>`,
      )
      .join(
        "",
      )}</div><span class="report-filter-count">${filtered.length} 份虚构记录</span></div><div class="report-list">${filtered.map((r) => `<a class="card report-row" href="#/reports/${e(r.id)}"><div class="report-date"><strong>${e(r.date.slice(8))}</strong>${e(r.date.slice(5, 7))}月</div><div class="report-content"><div class="report-meta">${pill(r.typeLabel, r.type === "intraday" ? "blue" : r.type === "data" ? "gray" : "")}<span>虚构历史演示</span></div><h3>${e(r.title)}</h3><p>${e(r.summary)}</p></div>${arrow}</a>`).join("")}</div><p class="report-status-note">没有真实建议、真实成交或收益记录。报告日期与内容均为固定演示；未来将逐条追踪实际操作与效果。</p><section class="section-spaced">${featureGrid()}</section>`;
  }
  function strategyDetail(id) {
    const s = data.strategies.find((item) => item.id === id);
    if (!s) return notFound("/strategies", "返回策略");
    return `<a class="back-link" href="#/strategies">← 返回策略</a><div class="detail-head"><span class="eyebrow">独立研究模块 · 虚构演示</span><h1>${e(s.title)}</h1><p>${e(s.summary)}</p></div><div class="detail-grid"><div class="detail-stack">${unavailable()}<section class="card detail-card"><h2>研究依据</h2>${list(s.basis)}</section><section class="card detail-card"><h2>数据限制</h2>${list(s.limitations)}</section></div><div class="detail-stack"><section class="card detail-card"><h2>验证状态</h2>${pill(s.statusLabel, id === "intraday" ? "blue" : "")}<p class="section-spaced">${e(s.validation)}</p><div class="timeline"><div class="timeline-item"><span>1</span><div><strong>数据核验 · 待完成</strong>来源、覆盖范围与真实账户时点</div></div><div class="timeline-item"><span>2</span><div><strong>成本与约束 · 待完成</strong>分账户费用、交易单位和可卖底仓</div></div><div class="timeline-item"><span>3</span><div><strong>独立验证 · 待完成</strong>样本外检验与后续观察</div></div></div></section><section class="card detail-card"><h2>相关记录</h2><a class="primary-link" href="#/reports/${id === "long-term" ? "demo-report-long" : "demo-report-t"}">查看历史演示报告 ${icon("arrow")}</a></section></div></div>`;
  }
  function reportDetail(id) {
    const r = data.reports.find((item) => item.id === id);
    if (!r) return notFound("/reports", "返回报告");
    return `<a class="back-link" href="#/reports">← 返回报告</a><div class="detail-head"><div class="report-meta">${pill(r.typeLabel)}<span>${e(r.date)} · 虚构历史记录</span></div><h1>${e(r.title)}</h1><p>${e(r.summary)}</p></div><div class="detail-grid"><div class="detail-stack">${unavailable(r.dataStatus)}<section class="card detail-card"><h2>当时的研究依据</h2>${list(r.basis)}</section><section class="card detail-card"><h2>风险与失效条件</h2>${list(r.invalidation)}</section><section class="card detail-card"><h2>数据限制</h2>${list(r.limitations)}</section></div><div class="detail-stack"><section class="card detail-card"><h2>数据状态与时间</h2>${dataLines(r)}<p class="tiny-note">历史记录保持当时的数据日期。页面刷新不会将其变成当前行情。</p></section><section class="card detail-card"><h2>可追溯信息</h2><dl class="data-lines"><div class="data-line"><dt>状态</dt><dd>${e(r.statusLabel)}</dd></div><div class="data-line"><dt>数据集</dt><dd>${e(r.datasetId)}</dd></div><div class="data-line"><dt>策略版本</dt><dd>${e(r.strategyVersion)}</dd></div><div class="data-line"><dt>数据来源</dt><dd>本地虚构演示夹具</dd></div></dl><details class="detail-disclosure"><summary>关于操作记录与效果评估</summary><p>本期没有真实成交确认或效果评估。未来只有用户确认的成交或新快照可以改变真实持仓，研究建议不会自动记作已执行。</p></details></section></div></div>`;
  }
  function positionDetail(accountId, symbol) {
    const a = data.accounts.find((item) => item.id === accountId),
      p = a?.positions.find((item) => item.symbol === symbol);
    if (!p) return notFound("/holdings", "返回持仓");
    const reference = state.hidden
      ? "••••"
      : new Intl.NumberFormat("zh-CN", {
          minimumFractionDigits: p.kind === "etf" ? 3 : 2,
          maximumFractionDigits: p.kind === "etf" ? 3 : 2,
        }).format(p.referencePrice);
    return `<a class="back-link" href="#/holdings">← 返回持仓</a><div class="detail-head"><span class="eyebrow">${e(a.broker)} · 虚构历史持仓</span><h1>${e(p.name)}</h1><p>${e(p.symbol)} · ${p.kind === "etf" ? "ETF / 份" : "股票 / 股"}</p></div><section class="card detail-card wide-card"><h2>持仓快照</h2><dl class="detail-facts"><div class="metric"><dt>总数量</dt><dd>${units(p.quantity)}</dd></div><div class="metric"><dt>历史可卖数量</dt><dd>${units(p.available)}</dd></div><div class="metric"><dt>每股 / 份成本 · 元</dt><dd>${price(p.brokerCost)}</dd></div><div class="metric"><dt>虚构参考价 / 元</dt><dd>${e(reference)}</dd></div><div class="metric"><dt>快照日期</dt><dd>${e(data.holdingsAsOf)}</dd></div><div class="metric"><dt>日内时刻</dt><dd>未知</dd></div></dl><details class="detail-disclosure"><summary>展开字段与 T+1 说明</summary><p>以上全是演示数据。历史可卖数量不是当前可卖额度；当日新买份额不得用于当日回转。券商显示成本可能受分红及做T影响，不可用于重建累计收益率。参考价只用于演示资产汇总，未接入行情。</p></details></section><div class="section-spaced wide-card">${unavailable()}</div>`;
  }
  function notFound(parent = "/", label = "返回首页") {
    return `<a class="back-link" href="#${parent}">← ${label}</a><section class="card empty-state"><h1>这份记录暂不存在</h1><p>请从列表选择已有的虚构演示记录。</p></section>`;
  }
  function render(focus = false) {
    const route = (location.hash.replace(/^#/, "") || "/").split("?")[0];
    const parts = route.split("/").filter(Boolean);
    const tab = parts[0] || "home";
    const known = ["home", "holdings", "strategies", "reports"].includes(tab);
    const name =
      { home: "首页", holdings: "持仓", strategies: "策略", reports: "报告" }[
        tab
      ] || "记录不存在";
    document.title = `养鹅计划 · ${name} · 演示`;
    const nav = navItems
      .map(
        ([path, key, label], i) =>
          `<a class="nav-link" href="#${path}" ${(path === "/" && tab === "home") || path === `/${tab}` ? 'aria-current="page"' : ""}>${icon(key)}<span>${label}</span><span class="nav-index">0${i + 1}</span></a>`,
      )
      .join("");
    document.querySelector(".desktop-nav").innerHTML = nav;
    document.querySelector(".bottom-nav").innerHTML = nav;
    if (!known) main.innerHTML = notFound();
    else if (tab === "home") main.innerHTML = home();
    else if (tab === "holdings")
      main.innerHTML =
        parts.length === 3
          ? positionDetail(parts[1], parts[2])
          : parts.length === 1
            ? holdings()
            : notFound("/holdings", "返回持仓");
    else if (tab === "strategies")
      main.innerHTML =
        parts.length === 2
          ? strategyDetail(parts[1])
          : parts.length === 1
            ? strategies()
            : notFound("/strategies", "返回策略");
    else if (tab === "reports")
      main.innerHTML =
        parts.length === 2
          ? reportDetail(parts[1])
          : parts.length === 1
            ? reports()
            : notFound("/reports", "返回报告");
    if (focus) {
      main.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
  }
  const dialog = document.getElementById("app-dialog");
  const dialogContent = document.getElementById("dialog-content");
  function openDialog(kind) {
    if (kind === "settings")
      dialogContent.innerHTML = `<h2 id="dialog-title">下一步，已经留好位置。</h2><p>这些入口用于展示后续流程，本期均未启用，不会保存或发送任何信息。</p><div class="dialog-menu">${featureKeys.map((key) => `<button data-dialog="${key}">${icon(featureIcons[key])}<span>${e(data.features[key].label)}<small>${e(data.features[key].description)}</small></span>›</button>`).join("")}<button data-dialog="install">${icon("home")}<span>添加到主屏幕<small>查看 iPhone 与电脑使用方式</small></span>›</button></div>`;
    else if (kind === "install")
      dialogContent.innerHTML =
        '<h2 id="dialog-title">放在主屏幕，随时打开。</h2><p>iPhone：用 Safari 打开同网预览地址 → 点击分享 → 添加到主屏幕。不同 iOS 版本的可用选项与表现可能不同。</p><div class="dialog-info">同网 HTTP 可用作网页与快捷入口，完整离线 PWA 需要可信 HTTPS。本期未部署 HTTPS，不要求安装证书，也不承诺手机断网可用。</div><p>电脑：双击“启动预览.cmd”；本机 localhost 支持离线演示缓存。关闭预览窗口即停止服务，缓存只包含公开虚构样例。</p><button class="dialog-back" data-dialog="settings">← 返回功能预留</button>';
    else if (kind === "status")
      dialogContent.innerHTML = `<h2 id="dialog-title">数据状态</h2><p>当前来源：本地虚构演示数据。所有金额、数量、参考价与研究记录均为演示，不会自动刷新。</p><div class="dialog-section">${dataLines()}</div><div class="dialog-info">${e(M.freshness(data.quoteAsOf).label)}。持仓仅有日期；银河现金未知。未知值没有替换成零，历史可卖量不代表当前额度。</div><p>策略均未验证，无法形成可靠建议。安全同步与真实数据入口尚未接入。</p>`;
    else if (featureKeys.includes(kind)) {
      const f = data.features[kind];
      const copy = {
        monthlyBudget:
          "未来将区分计划投入、实际到账、当月已用预算，并按账户检查现金。这里尚未设置预算，不把计划金额计入可用资金。",
        tradeConfirmation:
          "未来先查看成交差异，再由本人确认，新增持仓版本并保留历史。当前没有委托入口，也不会改变任何真实账户。",
        bark: "未来使用独立建议分组与脱敏摘要。本期不读取 Bark 密钥、不发送通知，也不改动原有 V1.1 模拟通知。",
        sync: "未来由 Windows 主动发布最小化只读报告，经身份验证后查看。本期无云端连接、上传、登录或持续费用。",
      }[kind];
      dialogContent.innerHTML = `<h2 id="dialog-title">${e(f.label)}</h2>${pill("功能预留 · 尚未启用", "gray")}<div class="dialog-info">${e(copy)}</div><p>本次仅体验软件原型，不录入真实持仓、账号或密钥。</p><button class="dialog-back" data-dialog="settings">← 返回功能预留</button>`;
    } else
      dialogContent.innerHTML =
        '<h2 id="dialog-title">让投资，慢慢生长。</h2><p>养鹅计划 V2 手机优先只读原型。长期积累与日内研究分开呈现，所有内容均为虚构演示。</p><div class="dialog-info">桌面可直接打开 index.html；本地启动预览后也可使用应用缓存。同一 Wi-Fi 手机可网页体验，完整离线 PWA 需要可信 HTTPS。没有部署任何云服务。</div><p>未接入真实持仓、行情、券商、Bark 或 AI 服务。原 V1.1 与 Draft PR #3 保持隔离。</p>';
    if (!dialog.open) dialog.showModal();
    document.getElementById("close-dialog").focus();
  }
  document
    .getElementById("close-dialog")
    .addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        dialog.close();
    }
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest(".skip-link")) {
      event.preventDefault();
      main.focus();
      main.scrollIntoView({ block: "start" });
      return;
    }
    const control = event.target.closest("button");
    if (!control) return;
    if (control.dataset.dialog) {
      openDialog(control.dataset.dialog);
      return;
    }
    if (control.dataset.action === "privacy") {
      state.hidden = !state.hidden;
      render();
      document.querySelector('[data-action="privacy"]')?.focus();
    }
    if (control.dataset.account) {
      state.account = control.dataset.account;
      render();
      document.querySelector(`[data-account="${state.account}"]`)?.focus();
    }
    if (control.dataset.reportFilter) {
      state.reportFilter = control.dataset.reportFilter;
      render();
      document
        .querySelector(`[data-report-filter="${state.reportFilter}"]`)
        ?.focus();
    }
  });
  window.addEventListener("hashchange", () => render(true));
  render();
  // file:// remains a fully interactive desktop preview; only secure contexts get SW.
  if (
    document.documentElement.dataset.standalone !== "true" &&
    location.protocol !== "file:" &&
    window.isSecureContext &&
    "serviceWorker" in navigator
  ) {
    window.addEventListener("load", () =>
      navigator.serviceWorker.register("./sw.js", { scope: "./" }).catch(() => {
        /* Core UI works without offline cache. */
      }),
    );
  }
})();

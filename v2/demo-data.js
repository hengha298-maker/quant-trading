/* All values below are fictional fixtures. Never connect this file to real accounts. */
(function (root) {
  "use strict";
  const data = {
    schemaVersion: 1,
    mode: "demo",
    datasetId: "demo-goose-mobile-001",
    sourceLabel: "虚构演示数据",
    holdingsAsOf: "2026-10-19",
    holdingsTimeKnown: false,
    quoteAsOf: "2026-10-19T15:00:00+08:00",
    generatedAt: "2026-10-19T16:20:00+08:00",
    syncedAt: null,
    transactionHistoryComplete: false,
    accounts: [
      {
        id: "DEMO_HB",
        broker: "华宝证券",
        coverage: "complete",
        cash: 12500,
        positions: [
          {
            symbol: "600900.SH",
            name: "长江电力",
            kind: "stock",
            quantity: 300,
            available: 200,
            brokerCost: 25.8,
            referencePrice: 26.14,
          },
          {
            symbol: "515450.SH",
            name: "红利低波50 ETF",
            kind: "etf",
            quantity: 2000,
            available: 2000,
            brokerCost: null,
            referencePrice: 1.028,
          },
        ],
      },
      {
        id: "DEMO_YH",
        broker: "银河证券",
        coverage: "partial",
        cash: null,
        positions: [
          {
            symbol: "515450.SH",
            name: "红利低波50 ETF",
            kind: "etf",
            quantity: 1200,
            available: 1000,
            brokerCost: 1.046,
            referencePrice: 1.028,
          },
        ],
      },
    ],
    focusAssets: [
      {
        symbol: "600900.SH",
        name: "长江电力",
        statusLabel: "长期研究中",
        summary: "关注现金流、分红持续性与配置；本期没有真实行情或买入建议。",
        actionable: false,
      },
      {
        symbol: "515450.SH",
        name: "红利低波50 ETF",
        statusLabel: "做T研究中",
        summary: "优先验证分钟样本、费用与可卖底仓；本期没有做T信号。",
        actionable: false,
      },
    ],
    strategies: [
      {
        id: "long-term",
        title: "长期加仓",
        status: "research",
        statusLabel: "研究中 · 暂不形成建议",
        summary: "让每月投入有计划，也给长期持有留出耐心。",
        basis: [
          "结合月度预算、实际到账资金和分账户持仓。",
          "分别研究股息、估值、现金流与长期趋势。",
          "满足账户约束后再研究分批加仓，不预测绝对最低点。",
        ],
        limitations: [
          "本页所有金额与参考价格均为虚构演示。",
          "没有接入真实财务、估值、分红与公司行为数据。",
          "月度预算尚未启用；缺少真实账户与费用核验。",
        ],
        validation: "未进行真实数据验证；仅演示研究流程。",
        actionable: false,
        symbols: ["600900.SH", "515450.SH"],
      },
      {
        id: "intraday",
        title: "日内做T",
        status: "research",
        statusLabel: "样本不足 · 无交易信号",
        summary: "先确认底仓、费用和数据，再谈日内机会。",
        basis: [
          "优先研究515450.SH，其次研究600900.SH。",
          "先买后卖也只能卖出原本可卖的底仓，当日买入不能当日回转。",
          "先卖后买需要评估无法回补、资金不足和底仓减少的风险。",
        ],
        limitations: [
          "尚未接入分钟行情；没有实时或历史做T信号。",
          "未完成样本外验证、成本压力测试和人工操作延迟评估。",
          "持仓示例只有日期，不能视作当前可卖数量。",
        ],
        validation: "未通过策略验收；不因每天开市而强行发信号。",
        actionable: false,
        symbols: ["515450.SH", "600900.SH"],
      },
    ],
    reports: [
      {
        id: "demo-report-1019",
        title: "先把数据看清楚，再作决定",
        date: "2026-10-19",
        type: "data",
        typeLabel: "数据检查",
        summary: "演示两个账户的完整性检查：银河现金未知、快照缺少日内时刻。",
        statusLabel: "演示 · 数据不足",
        basis: [
          "华宝示例标记已录全；这一声明不等于已核验。",
          "银河示例为部分录入，可用现金保留未知。",
          "总额只汇总已知部分，不展示全账户收益率。",
        ],
        limitations: [
          "没有真实账户数据。",
          "参考价格为固定虚构样例，不随系统时间刷新。",
        ],
        invalidation: [
          "所有金额、数量和价格均不得用于真实交易。",
          "即使打开页面或刷新，也不会更新行情或持仓时点。",
        ],
        dataStatus: "仅虚构夹具；行情非实时；持仓时刻未知；无云端同步。",
        actionable: false,
        generatedAt: "2026-10-19T16:20:00+08:00",
        quoteAsOf: "2026-10-19T15:00:00+08:00",
        holdingsAsOf: "2026-10-19",
        syncedAt: null,
        datasetId: "demo-goose-mobile-001",
        strategyVersion: "prototype-only",
      },
      {
        id: "demo-report-long",
        title: "长期加仓：先确认预算与现金",
        date: "2026-10-16",
        type: "long-term",
        typeLabel: "长期加仓",
        summary: "演示长期研究所需条件；预算未启用，暂不生成加仓数量。",
        statusLabel: "演示 · 研究中",
        basis: [
          "月度预算应区分计划投入与实际到账。",
          "两个账户不能共用同一笔可用资金。",
          "财务与股息数据必须有来源和公布时点。",
        ],
        limitations: [
          "本期没有实际预算、财务或费用数据。",
          "券商显示成本不等于真实累计收益。",
        ],
        invalidation: [
          "数据缺失时不能把未知现金当作零或当作可用资金。",
          "未通过研究验收前，不提供操作价格和数量。",
        ],
        dataStatus: "虚构历史演示；财务、费用与策略均未核验。",
        actionable: false,
        generatedAt: "2026-10-16T16:10:00+08:00",
        quoteAsOf: null,
        holdingsAsOf: "2026-10-16",
        syncedAt: null,
        datasetId: "demo-long-term-001",
        strategyVersion: "prototype-only",
      },
      {
        id: "demo-report-t",
        title: "做T观察：没有可靠信号，也是一种结果",
        date: "2026-10-15",
        type: "intraday",
        typeLabel: "日内做T",
        summary: "演示没有分钟样本和真实费率时，系统如何保留不可用状态。",
        statusLabel: "演示 · 无信号",
        basis: [
          "先核对可卖底仓和账户资金。",
          "扣除双边费用、价差与滑点，再评估研究价值。",
          "先买后卖不得卖出当日新买份额。",
        ],
        limitations: [
          "没有分钟样本、样本外结果或真实成交记录。",
          "本原型不计算或承诺做T收益。",
        ],
        invalidation: [
          "无法确认当前底仓或费用时，不形成数量建议。",
          "过期分钟数据不能作为当前操作条件。",
        ],
        dataStatus: "虚构历史演示；分钟行情未接入；策略未验证。",
        actionable: false,
        generatedAt: "2026-10-15T16:10:00+08:00",
        quoteAsOf: null,
        holdingsAsOf: "2026-10-15",
        syncedAt: null,
        datasetId: "demo-intraday-001",
        strategyVersion: "prototype-only",
      },
    ],
    features: {
      monthlyBudget: {
        enabled: false,
        value: null,
        label: "月度投资预算",
        description: "未来区分计划投入、实际到账与已使用预算。",
      },
      tradeConfirmation: {
        enabled: false,
        label: "成交确认",
        description: "未来经本人确认后新增持仓版本；建议不会直接改变持仓。",
      },
      bark: {
        enabled: false,
        label: "Bark 提醒",
        description: "本期不读取密钥、不发送通知、不改动原有提醒。",
      },
      sync: {
        enabled: false,
        label: "安全同步",
        description: "本期不连接云服务；未来只读发布须独立认证。",
      },
    },
  };
  function freeze(value) {
    if (value && typeof value === "object") {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  }
  freeze(data);
  if (typeof module !== "undefined" && module.exports) module.exports = data;
  root.GooseDemo = data;
})(typeof globalThis !== "undefined" ? globalThis : this);

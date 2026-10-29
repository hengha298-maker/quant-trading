# Opportunity radar and fixed-rule strategy research

The existing investment entry now includes two separately ranked watchlists. The reviewed catalog is in `radar_catalog.json`: domestic Shanghai equity ETFs and ordinary liquid stocks, not an all-market or historical index-membership universe. Editing this bounded source list requires reviewing identity aliases, price ticks, calendar coverage and applicable trading rules.

## Data and refresh

Tencent public quote and daily endpoints provide the first usable source. They are not independent feeds, exchange-authorized real-time infrastructure or an uptime guarantee. A batch identity/quote check precedes history use. Source timestamps, receipt times, units, OHLC, share volumes, quote/daily price scale, full exchange sessions and immutable raw responses are retained in ignored local research storage. No credentials or portfolio records are sent with these requests.

Only the last complete session is used for factors. Same-day snapshots are reusable only after a fresh scale check; on a new session raw history is extended with an exact overlap check, while provider front-adjusted history is refreshed as a complete basis. Missing sessions, unreviewed names/status, raw revisions, suspected ETF unit jumps, missing adjusted fields and inactive volume fail closed. A missing day is not automatically labelled a suspension or filled with an invented price. The verified calendar boundary is explicit; later dates stop research until reviewed.

## Ranking

ETF and stock scores use fixed, documented components: trend 25, momentum/relative strength 20, breakout location 10, volume 10, volatility/drawdown 15, estimated liquidity 10 and portfolio diversification 10. Scores are research ordering, never probabilities. Liquidity uses closing price times shares as a disclosed approximation, not reported transaction value or guaranteed execution.

Candidates require score at least 65, ordered close/MA20/MA60, positive short momentum, nonnegative 60-session relative momentum, bounded volatility/drawdown, sufficient estimated turnover and holding-correlation evidence. Existing holdings are shown as holdings research, not newly discovered opportunities. Highly correlated additions are excluded when the available securities snapshot is concentrated. Household allocation, ETF constituent overlap and current buying power remain unverified; no trade quantities are proposed. Each group requires a minimum number of comparable histories (five ETFs or four stocks), returns at most five candidates and may return none.

## Reproducible comparisons

Five fixed rules share the existing integer-cent portfolio model: buy and hold, MA20/MA60 trend, 20-day breakout/10-day exit, 60-day momentum relative to a large-cap ETF proxy, and low-volatility/drawdown control. The proxy cannot be its own relative-strength test.

All use identical research capital, 100-share/unit lots, T+1, explicit commissions/minimums, conservative tick-rounded slippage and stock sell tax assumptions. Signals use prior completed sessions; fills use next raw opening prices. Known publication delays prevent early use. Opening limit queues, zero-volume days, slippage beyond assumed limits and suspected corporate-action reference days block fills. Special historic limits and order-book queue execution are not reconstructed.

Fixed historical folds are training September 2024–June 2025, validation July–December 2025 and held-out 2026 through the actual data cutoff. Each fold independently starts with equal capital; parameters and weights are not selected using held-out performance. These retrospective folds are not prospective validation. The current surviving catalog has selection/survivorship bias, and provider-adjusted data can be revised retrospectively: point-in-time factors are not certified. No claim of leakage-free investable historical alpha is made.

Adjusted prices are used only for indicators. Raw prices value fills and accounts; complete cash dividends, tax, corporate actions and dividend reinvestment are not reconstructed. Consequently reported price-path returns are not total returns. Annual return is suppressed below 252 sessions or 365 calendar days; volatility and Sharpe use 252-session scaling and zero risk-free rate. Turnover is total buy plus sell notional divided by mean closing NAV, without halving. Full curves, orders, costs, immutable input hashes and model revisions are downloadable through authenticated private routes.

## Existing daily flow and safety

No new application, entry or scheduled task is introduced. The existing analysis flow runs the bounded radar after portfolio analysis, reserving time and quote freshness for the existing report. Cache misses or budget exhaustion produce explicit unavailable/partial states, never yesterday's candidates disguised as today's. Old report dates, account-source changes and unknown calendar dates suppress current candidates without modifying archived reports.

Only bounded public candidate counts enter the existing notification summary. Candidate names, positions, account amounts and private URLs do not. Existing notification approval, date claims, no-retry and deadline gates remain unchanged. Development tests inject transports and use fictional local records; they do not send real notifications or orders.

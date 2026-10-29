# Event evidence, causal research and dividend-aware accounts

The existing dashboard keeps its original price lab and adds separate event-aware research. No broker ledger, order permission, application, notification approval or scheduled task is changed.

## Reviewed evidence and bounded claims

`corporate_actions.json` contains normalized public facts and document hashes, publication dates, exact source URLs and page/section locators. Original documents remain in ignored local research storage. A document archive must still match its recorded hash before a cached study can claim complete cash-flow coverage.

Cash events distinguish announcement, conservative information availability, record date, ex-date and payment date. Share changes distinguish record, ex-date and first tradable new-share date. Suspensions use their own start/end/resumption schema. Missing fields remain missing; a proposed dividend is not a completed payment event. Conflicting or duplicate events fail validation. Price jumps alone never create events.

Annual distribution tables are reconciled with individual implementation announcements; share-change sections supply negative evidence for a declared interval. Later annual reports audit completeness retrospectively and are never treated as advance trading information. Coverage applies only to explicitly reviewed dates and event types. A few correct events do not imply full-horizon coverage. Provider front-adjusted prices do not certify dividends, tax or corporate actions.

Locally archived evidence currently supports bounded accounts for 515450 in 2024/2025, 600900 in 2025, and a separate raw-only 512660 study in 2025 (the latter remains excluded from current qfq ranking). For 600900, original January/July payment notices reconcile to opening dividends payable plus declared distributions minus closing payable, with unchanged shares in the annual report. A dividend declared in December 2025 but paid in February 2026 is not counted as 2025 cash. These are issuer-level public reconciliations, not private account reconciliation or proof of a shareholder's net tax. No full current 2026 cash-flow horizon is certified.

## Five distinct meanings

1. Raw historical market prices remain execution/valuation inputs.
2. The legacy provider-adjusted indicator lab remains visible with retrospective-revision warnings.
3. The new forward event index links raw prices with only publicly available ex-date cash/share terms. Later events never rescale earlier rows. This indicator index is not an execution price or an actual reinvestment account.
4. Event-aware price-only accounts process verified share entitlements but exclude dividend cash. Dividend-retention accounts recognize receivables on ex-date and cash at announced payment-day close; dividends are not external funding and are never added to adjusted-price portfolio returns.
5. Reinvestment accounts may spend credited dividend cash from the next session, while still invested and not under an exit signal. Minimum lots, costs, available cash and failed fills apply. Unaffordable residual cash stays cash; no fractional reinvestment units are invented.

Record-date closing holdings determine rights. Selling afterward does not erase an earned dividend; buying on ex-date creates no rights to that distribution. Share changes preserve acquisition cost totals and change quantity/unit cost, with explicit listing restrictions. Unsupported fractional share settlement blocks a complete result. Final eligible odd lots may be sold under the declared ordinary-share assumption.

ETF tax exemption is an explicit reviewed-announcement assumption. Where stock tax history cannot be reconstructed, a uniform 20% research reserve is labelled as a conservative scenario; separate before-dividend-tax scenarios keep the same transaction-cost assumptions. This is not a reconstruction of holding-period differentiated tax or a real account receipt. Every strategy uses the same capital, calendar, fee/slippage assumptions and interval. Costs are reflected once in fills/cash; displayed slippage is already embedded, not deducted twice.

## Executability and point-in-time limits

Rules use only prior completed observations. Known delayed publication in either security or benchmark blocks early use. Unknown historical publication time is explicitly assumed available next session, not certified. Prior-close limit references are adjusted only by verified cash/share terms; opening limit queues, fills outside assumed limits or observed OHLC, zero volume and listing/T+1 restrictions can prevent fills.

Officially evidenced suspension gaps can be represented only in a separate non-tradable valuation view, preserving source rows. These stale marks are not new market prices. Windows containing such marks pause fresh indicator signals; a corporate action during a halt blocks the unsupported valuation combination. Unknown gaps are rejected. Diagnosing an old quarantine does not automatically restore current ranking eligibility.

Historical bar vintages and point-in-time universe membership are not certified. Current surviving securities were manually selected. Missing corporate actions can affect indicators even when price fields pass basic validation; the dashboard shows this uncertainty rather than declaring complete total returns. A verified window can be shorter than the current price horizon, and must never be directly compared against a different return interval.

## Strategy quality

The five fixed rules retain identical chronological training, validation and historical holdout boundaries. No parameters or score weights are selected using holdout performance. Dependent daily excess returns versus the corresponding buy-and-hold account receive paired circular moving-block bootstrap diagnostics (10/20-session sensitivity, fixed seeds, 4,999 repetitions). Holm correction treats all rule/security comparisons within each fold/block setting as one family, retaining untestable hypotheses conservatively. Monte Carlo p-value resolution must support that family size; incomplete or time-aborted families produce no partial significance conclusion. Return arithmetic uses integer cents/Decimal; floating point is limited to statistical sampling.

These diagnostics approximate weakly stationary dependence, do not remove survivorship/selection bias and are not a count of independent winners. Short samples and missing folds are explicit. Even a positive statistical screen is not labelled proven stable advantage without the missing prospective and point-in-time evidence. The current conclusion is **not demonstrated superior to buy and hold**.

## Future exchange calendars

The shared calendar gate can extend beyond its old boundary only through a committed reviewed calendar package. A future year must enumerate every calendar date with an explicit open/closed classification and basis, official exchange evidence hashes/publication times, reviewer, review time and classification checksum. Downloads and drafts cannot activate themselves; dependency forecasts and weekday inference are not accepted as reviewed sessions.

The reviewed package currently has no active 2027 calendar. Requests for unreviewed 2027 dates still fail through the same gate used by daily analysis. Before adding a real future package, archive and review the official complete schedule, validate all classifications and downstream report compatibility, then commit the reviewed record. This creates no task or retry mechanism.

## Daily operation and persistence

New studies use immutable hashed research artifacts served through the existing authenticated download route. Verified fixed windows can be reused independently of a new day's prices. Work is time-bounded; a cold cache may produce an explicit partial research state while preserving the existing report/notification deadline. No old incomplete result is relabelled complete after evidence changes. Private account data, backups, credentials and delivery claims are outside this public evidence catalog and remain unchanged.

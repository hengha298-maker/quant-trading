# Relative momentum: symmetric benchmark audit

The previous event-account model applied known subject dividends to its indicator sequence while accepting incomplete benchmark corporate-action coverage. `benchmarkActionCoverage` was diagnostic only. Its relative-momentum price/cash conclusions are historical, pre-correction records, not a valid current total-return comparison.

## Calculation contract

- Price relative momentum uses **unadjusted prices on both sides**. Both relative 60-session return and the subject's 20-session momentum filter use raw prices. It is price momentum, not total-return momentum. The legacy price lab and radar relative-strength component use the same explicit raw/raw definition; other indicators keep their existing definitions.
- Dividend-inclusive relative momentum requires **complete reviewed cash/share-action coverage for both instruments, including all 61 warm-up sessions through the study end**, and matching original-document hashes. Its two signal sequences use the same forward-linked gross entitlement index, never vendor qfq. Publication/availability is checked before ex-date. Investor tax scenarios affect the separately simulated account, not one side of the signal ratio.
- The gross entitlement index is not proof of cash available to reinvest. Record-date ownership, payment-close cash and next-session reinvestment remain separately modeled.
- Missing intervals, provisional events, unknown payment/listing dates, missing or mismatched relevant originals or late announcements block the affected dividend-inclusive relative strategy. A blocked result has no return/curve/trades, a fixed reason and `applicable=false`; it is not an all-cash 0% strategy.
- A benchmark cannot supply relative-momentum evidence against itself. It remains excluded from the comparison family.

The other four rules do not depend on benchmark indicators, publication times, stale marks or event-index availability. Missing benchmark action evidence does not suppress their accounts or change their outputs. Adding official 510300 events can legitimately change 510300's **own** non-relative accounts; that is separately reported, not hidden as an unrelated-rule regression.

Price-relative and dividend-relative columns now have different declared signal definitions. Differences include trading-path changes as well as cash distributions. They cannot be read as a pure dividend contribution or mixed into a single unlabelled momentum ranking.

## Reviewed benchmark evidence

The public catalog records implementation notices and original SHA256 hashes for 510300 distributions in January 2024, June 2025 and January 2026. Full 2024/2025 annual distribution tables reconcile to the original notices, and share-change sections show no splits in those years. Per-10-unit announcements are normalized to CNY per unit. Announcement, conservative next-session availability, record, ex-date and payment dates remain distinct; a distribution-reference year is not its payment year.

Only 2024-01-01 through 2025-12-31 is reviewed complete. The January 2026 notice does not certify all of 2026. Therefore current 2026 dividend-relative conclusions remain blocked. Subject coverage can also be incomplete during warm-up even if its account period is covered.

Annual reports audit completeness retrospectively, not as advance trading information. Historical price vintages, point-in-time universe membership, actual taxes/fills and independent price feeds remain uncertified.

## Immutable evidence and recomputation

Event-study revision 3 includes the effective subject/benchmark evidence certificates in its input hash. Missing or corrupted benchmark files generate a separate degraded artifact; old certified artifacts are never rewritten or silently reused. Rechecking certificates before return also rejects evidence changes during computation. Restoring verified originals cannot leave a stale blocked cache under the same effective certificate.

Legacy price-lab revision 4 hashes benchmark raw observations as well as existing inputs. Statistical summaries identify the corrected raw/raw relative definition. Old revision-2 event studies and their 47-comparison/21-winner summaries remain preserved and labelled historical. New studies and statistics are rerun against exactly matched archived price inputs; equality of winner counts is not equality of results or evidence of advantage.

Tests use fictional ex-dividend paths, separate source originals, deliberate hash damage, warm-up gaps, provisional/late/missing events, delayed benchmark publication, exact invariance of the independent rules and immutable-cache checks. Browser tests confirm the block/basis labels and retained encrypted recovery. No brokerage account, scheduled task or notification is executed by these tests.

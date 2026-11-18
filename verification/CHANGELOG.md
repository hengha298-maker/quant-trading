# 2026-11-19 bounded public network validation

Basis: ebde92f / offline validation 7760ec2 (successful run 44340543681 retained).
Change: add public provider compatibility and unauthenticated existing entry diagnostics. Existing parsers/rules are unchanged. No private credentials, accounts, artifacts, schedules or production writes. Synthetic quantities only; no report body is logged or uploaded.
Files: cloud_probe.py, public-network.yml; add path filter to linux-offline.yml so the already-passed offline job is not repeated for diagnostic-only changes.
Checks: changed-file AST/security/hash check before commit; actual Linux results recorded separately after execution.
Rollback: revert only this diagnostic change on this isolated branch; main, PR10 and production stay unchanged.

Targeted correction after first run: the collector rejected the separate cloud temporary root before any request. Add explicit data_root, retain strict investment/work-only, no links/publication paths, and pass root from engine. No market/strategy threshold changes. Add regression and one bounded diagnostic retest. Browser dump gets a bounded virtual-time budget.

Task-2 fixture correction: retain testData marker and production V2 rejection; compute synthetic history views with persist=False. Saved engine report remains real-public-market / fictional-account only. Do not repeat successful entry probes.
Linux D1 verification copies existing candidate protocol and seven migrations verbatim, changing only the local runtime import in the nine existing tests. No production binding, secret, schedule or deployment configuration is included.
Protocol retest: seven checks passed on Linux; the rejected-future-report fixture used 2026-11-19 which became today in Asia/Shanghai. Replace that test input with relative tomorrow, preserving the production provenance rule and all assertions. The dependent ninth test failed only because the eighth stopped early.

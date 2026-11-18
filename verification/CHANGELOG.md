# 2026-11-19 bounded public network validation

Basis: ebde92f / offline validation 7760ec2 (successful run 44340543681 retained).
Change: add public provider compatibility and unauthenticated existing entry diagnostics. Existing parsers/rules are unchanged. No private credentials, accounts, artifacts, schedules or production writes. Synthetic quantities only; no report body is logged or uploaded.
Files: cloud_probe.py, public-network.yml; add path filter to linux-offline.yml so the already-passed offline job is not repeated for diagnostic-only changes.
Checks: changed-file AST/security/hash check before commit; actual Linux results recorded separately after execution.
Rollback: revert only this diagnostic change on this isolated branch; main, PR10 and production stay unchanged.

Targeted correction after first run: the collector rejected the separate cloud temporary root before any request. Add explicit data_root, retain strict investment/work-only, no links/publication paths, and pass root from engine. No market/strategy threshold changes. Add regression and one bounded diagnostic retest. Browser dump gets a bounded virtual-time budget.

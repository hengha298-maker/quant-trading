# Investment research and reconciled local ledgers

Use the existing Start-Investment.cmd entry. This change adds no application or scheduled job. Deployment paths, notification credentials and user-specific operating instructions belong in local audit records, never in public source.

## Available features

- Separate broker snapshots with user-reviewed CSV/XLSX or manual import, immutable source hashes and unknown-value preservation.
- Incremental executions, cash, dividends and fees: preview, explicit confirmation, duplicate/conflict detection, append-only undo and declared coverage intervals.
- End-of-day statements and evidence-gated quantity, cash, acquisition cost, realized/unrealized profit and dividend reconciliation. Calculations distinguish execution, settlement and actual credit dates. Confirmed execution times order same-day trades; ambiguous order pauses cost calculations.
- Conditional research plugins, price-history metrics and separately labelled historical backtests. Price returns are not verified dividend-inclusive strategy returns. New research rules do not inherit validation from older strategies.
- Existing private views display research, accounts, evidence quality and runtime status. No broker order or cancellation interface is exposed. Official account authorization and real export-format acceptance remain external prerequisites.

## Complete backup and recovery

The new encrypted package includes immutable Windows accounting records and this browser origin's existing data. An older mobile encrypted backup can be explicitly attached; other devices or origins cannot be read automatically. The legacy mobile backup button alone does not include Windows records.

Passwords stay in browser WebCrypto. Authenticated encryption, versioned manifests, checksums, preflight conflict checks and atomic publication protect recovery. Recovery only merges: it never overwrites originals, silently changes an existing effective position baseline or silently voids an active batch. Credentials, delivery claims and scheduling controls are excluded.

The existing mobile encryption and IndexedDB format remain readable. A browser save failure after Windows publication is reported as partial completion; retry is idempotent rather than falsely presented as a distributed transaction.

See [ledger and backup formats, formulas, limits and required exports](LEDGER-AND-BACKUP.md). Private accounting data and backups remain Git-ignored. No private deployment metadata or financial examples from real accounts belong in tests or public logs.

## Validation

Run `python -B -m unittest discover -s investment/tests -q` and `node --test investment/tests/*.test.mjs v3/tests/*.test.mjs`. The isolated browser regression uses fictional records and temporary profiles. Automated correctness checks, actual broker-file acceptance and live broker authorization are distinct states.

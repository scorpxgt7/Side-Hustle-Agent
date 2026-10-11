# Changelog

## 2026-10-11 — Repository repair and Phase 1 reconstruction
- Restored mandatory context files absent from the tracked repository.
- Added reviewable Python modules and tests based on the documented Phase 1 interface. **Not verified identical to archived source.**
- Corrected expected-value treatment of failure losses and introduced finite numeric input validation.
- Added pull-request CI and clarified paper-only restrictions.
- Local reconstruction verification: 8 tests passed; Python compileall passed. GitHub CI must verify the committed version independently.
- Remains blocked for autonomous real-money use: no reconciled ledger, reservations, idempotent orders, security review, or verified profitability.

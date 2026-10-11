# QA and Release Gates

## Required
- Test valid and invalid economics, including failure-loss probabilities and nonfinite values.
- Test single/aggregate capital, daily-loss, drawdown, invalid policy rejection, and paper-only decisions.
- Test SQLite decision and audit persistence.
- Run `python -m pytest` and `python -m compileall -q src tests` locally and in CI.

## Hard boundaries
No live finance integrations, unrestricted credentials, claims of real profits, or simulated orders masquerading as fills.

## Outstanding blockers
No paper ledger reconciliation, capital reservations, duplicate-action protection, evidence freshness screening, external provider tests, or comparison with the compressed archive.

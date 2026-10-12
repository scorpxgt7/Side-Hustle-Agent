# CHANGELOG.md

## 2026-09-28 â€” Initial scaffold

### Added
- Project README and operating rules.
- Persistent context/change-control documentation.
- Architecture and risk-boundary documentation.
- Placeholder project configuration.
- Deterministic domain-model package scaffold.
- Paper execution boundary scaffold.
- Opportunity worker interfaces.
- Test scaffold.

### Security
- Explicit prohibition on storing debit-card numbers, CVV, bank passwords, private keys, seed phrases, or API secrets.
- Live execution disabled by default.

### Verification
- Repository structure created successfully.
- No live financial integrations are present.

### Remaining work
- Implement domain models and risk governor.
- Implement paper ledger.
- Implement discovery adapters.
- Add unit/integration tests.
- Add persistence.
- Add observability and audit querying.

## 2026-09-29 â€” Phase 1 control plane

### Added
- Deterministic `EconomicEvaluator` using `Decimal` arithmetic.
- `EconomicEvaluation` with expected net, expected value, return on capital, risk-adjusted score, and viability.
- Expanded deterministic `RiskGovernor` with allocation, deployable-capital, probability, confidence, liquidity, daily-loss, drawdown, leverage, and positive-EV checks.
- `PortfolioState` for daily allocation, realized P&L, peak equity, current equity, and drawdown.
- SQLite persistence for opportunities, decisions, paper ledger entries, and audit events.
- `Phase1Engine` orchestration for evaluation, risk gating, persistence, and audit recording.
- Project `pyproject.toml` and pytest configuration.
- Phase 1 unit/integration tests.

### Security
- Live execution remains disabled.
- No payment-card, banking, brokerage, exchange, withdrawal, or private-key credentials were added.
- Risk decisions remain deterministic and cannot be overridden by an LLM.

### Verification
- Full test suite: **7 passed**.
- Negative expected value is rejected.
- Daily-loss and drawdown breaches are rejected.
- Successful candidates are classified as `PAPER_TEST`, never `EXECUTE`.
- Persistence and audit creation verified with a temporary SQLite database.

### Remaining work
- Add explicit paper order/position state and idempotency enforcement.
- Add daily simulation runner and portfolio reconciliation.
- Add discovery workers/adapters with source provenance and freshness checks.
- Add observability and dashboard/API layer.
- Define jurisdiction-specific tax assumptions before any real-money evaluation.

## 2026-10-11 — Restore and harden Phase 1

- Restored archived source, configuration, tests and required docs without caches.
- Replaced probability-discounted costs with explicit outcome-based EV; persisted
  failure revenue/cost and leverage assumptions in an additive SQLite table.
- Validated finite Decimal inputs/ranges; enforced actual opportunity leverage,
  loss/drawdown equality stops, and canonical risk evaluation on every path.
- Made decision/audit persistence atomic and opportunity IDs immutable; direct
  repository writes now require caller-managed transactions.
- Rejected non-finite paper/ledger amounts and retained unique ledger event IDs.
- Added tests and GitHub Actions for Python 3.11/3.13; updated operating docs.
- Verification: 37 tests passed on Python 3.11.9; source compilation passed.
- Live execution remains disabled. No secrets introduced. Portfolio reservations,
  reconciliation, source freshness and live-release controls remain out of scope.

## 2026-10-12 — Close PR #2 review defects

- Closed the zero daily-loss-budget loophole, including portfolios with gains.
- Added exact rational intermediates for Decimal outcome/control arithmetic and
  a fully specified 50-digit ratio context; caller settings cannot change results.
- Bounded Decimal coefficient digits, exponent and adjusted magnitude to prevent
  unsupported arithmetic sizes. Paper balance updates validate before mutation.
- Rejected negative persisted ledger amounts, unsupported operation kinds and
  invalid opportunity/event IDs. Decision references must match their opportunity.
- Added effective opportunity/limits/state/evaluation snapshots, checks, decision
  ID and arithmetic/risk versions to the existing atomic audit transaction.
- Corrected the active build prompt's EV rule and current README phase status;
  appended authoritative context, procedures and QA coverage without rewriting history.
- Verification: 71 tests passed locally on Python 3.12.14 with pytest 8.3.5;
  source compilation and git diff whitespace checks passed. Python 3.11/3.13
  remain the required CI matrix; inspect their status on the latest PR head.
- No execution capabilities enabled. Paper reservations, lifecycle, reconciliation,
  freshness checks and live release remain future work.

## 2026-10-12 — Persistent paper accounts and atomic reservations

- Added explicit synthetic account genesis, immutable limits/currency, persistent
  cash/reservation state and audited halts. PHP/USD profiles use exact cents.
- Added a version/checksum-guarded additive paper schema, foreign keys and unique
  account/request/order references. Legacy ledger amounts never become paper cash.
- Added Repository.transaction with BEGIN IMMEDIATE, bounded busy timeout and
  rejected nesting. Phase1Engine now uses this owned transaction; direct legacy
  writes still participate in caller-managed transactions.
- Added atomic submit-and-reserve: effective account state is loaded under the
  write lock, screening/prospective loss checks run, and decision/request/order/
  reservation/account update/audit commit together or roll back.
- Canonical request identities return stored results on equivalent retries and
  reject conflicting inputs. Accepted observations cannot be reserved twice in an
  account or reinterpreted in another currency. No cash is spent by reservations.
- Cash-cost contracts explicitly distinguish refundable principal from expensed
  purchases; conservative loss allowance includes all reserved cash. Fees cannot
  be silently omitted, and expensed principal is not charged a second time.
- Added reservation/genesis consistency checks that halt accounts on discrepancies;
  this is not full journal/account reconciliation. No unhalt or fill path added.
- Verification: 115 tests passed locally on Python 3.12.14 / pytest 8.3.5;
  compilation and diff whitespace checks passed. Includes independent-connection
  races, SQL/audit failures, process exit before commit/response, backup restoration,
  schema compatibility failures, cross-day retention and currency safeguards.
- All real execution/card/withdrawal capabilities remain disabled. Next work is
  the supported paper order lifecycle and balanced settlement/accounting.

## 2026-10-12 — Paper lifecycle implementation intent

Implement the next PAPER-only increment in a stacked follow-up PR: full fills,
explicit cancellation/deadline expiry, and full settlement. Add balanced immutable
journal records, retain open-position loss capacity, and migrate known reservation
schemas transactionally without importing legacy cash. Verify money movements,
retry/conflict handling, transition races and rollback before claiming completion.
No live execution, payment-card access, external adapters or daily reset is enabled.

## 2026-10-12 — Completed paper lifecycle/accounting increment

- Added full PAPER fill/cancel/explicit-expire/settle transitions with expected order
  versions, durable command results, conflict detection and atomic audit linkage.
- Added cost-valued refundable assets, expensed purchase accounting, entry/exit fees,
  full losses/salvage and closed-position P&L without charging costs twice.
- Transferred loss capacity from reservations to positions and retained open all-in
  allocation. Added a fixed aggregate deployment check that includes fees after gains.
- Added balanced, immutable journal history, sealed posting manifests and internal
  account/order/position/receipt/audit projection checks. Corruption halts movements;
  intact halted accounts can reduce exposure without automatic unhalting.
- Added aware-UTC immutable deadlines; expiry is explicit and late fills cannot spend.
- Added a transactionally guarded v1-to-v2 migration, retaining request/order/audit
  history and deriving genesis journals only from explicit synthetic funding records.
- Updated active README/PROMPT and appended authoritative context, API procedures,
  migration guidance and QA coverage; previous documentation remains historical.
- Verification: 185 tests passed locally on Python 3.12.14 / pytest 8.3.5; source
  compilation and diff whitespace checks passed. Coverage includes independent money
  expectations, command races, SQL/audit rollback, process exit/retry, backup/restore,
  migration failure rollback, corruption halts and Decimal-context independence.
  Required Python 3.11/3.13 CI must pass on the published PR head before merge.
- Default execution flags remain false. No real funds/card/live/external adapters.
  Next: independent reconciliation reporting, then valuation/day-boundary and reviewed
  repair/unhalt controls, source freshness and daily orchestration before live release.

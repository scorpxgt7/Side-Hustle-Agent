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

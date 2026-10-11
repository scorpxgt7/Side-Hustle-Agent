# PROJECT_CONTEXT.md

## Purpose

Build an autonomous side-hustle opportunity engine that searches broadly for legitimate online income opportunities, evaluates expected net economics, runs controlled experiments, records outcomes, and eventually allocates a bounded capital pool under deterministic risk controls.

## User objective

The system should look every day for ways to generate income online, potentially including algorithmic trading, arbitrage, crypto-related strategies, compute/mining economics, affiliate/business opportunities, digital products, services, data products, and other lawful opportunities.

The objective is **risk-adjusted long-term net profit**, not maximum daily activity or maximum leverage.

## Current phase

Phase 0 â€” architecture/scaffold/paper mode.

## Capital policy

- No debit-card credentials in code or prompts.
- No unrestricted account access.
- No withdrawals/transfers by the agent.
- No live trading in the initial build.
- All strategies begin in discovery or paper mode.
- Future capital must be isolated and explicitly bounded.
- Reserve capital must remain unavailable to individual workers.

## Core architecture

```text
Executive Orchestrator
        |
        +-- Opportunity Discovery
        |      +-- Markets
        |      +-- Arbitrage
        |      +-- Online Business
        |      +-- Compute/Mining Economics
        |      +-- Affiliate/Digital Products
        |
        +-- Economic Evaluation
        |
        +-- Risk Governor (deterministic)
        |
        +-- Paper Execution
        |
        +-- Ledger / P&L
        |
        +-- Audit Log
        |
        +-- Human Approval Boundary
```

## Required change-control protocol

Every change must:

1. Read this file, `CHANGELOG.md`, `OPERATIONS.md`, and `QA.md`.
2. State the intended change and affected components.
3. Check for conflicts with capital/risk rules.
4. Implement the smallest safe change.
5. Run relevant tests/verification.
6. Update `CHANGELOG.md`.
7. Update this file if architecture, behavior, interfaces, limits, dependencies, or decisions changed.
8. Update `OPERATIONS.md` if operating procedures changed.
9. Update `QA.md` if test coverage or acceptance criteria changed.
10. Record unresolved risks/blockers.

## Current decisions

- The LLM is an analyst/orchestrator, not the final authority for monetary limits.
- Risk limits are deterministic and must not be overridden by model output.
- The system must be able to return `NO_ACTION`.
- Opportunity evaluation must subtract fees, slippage, infrastructure costs, taxes where applicable, and expected failure/loss costs when estimable.
- Every executed/simulated action must have an audit record.
- Live integrations will use adapter interfaces and least-privilege credentials.

## Known blockers

- GitHub repository: scorpxgt7/Side-Hustle-Agent; changes reviewed through PRs.
- No brokerage/exchange/payment integrations have been authorized.
- No starting capital amount or jurisdiction-specific tax configuration has been supplied.
- No live execution should be enabled until security and QA gates exist.

## Next implementation step

Implement the deterministic domain model for opportunities, capital limits, risk decisions, paper orders, ledger entries, and audit events, then add worker interfaces and a daily simulation loop.

## Phase 1 implementation state â€” 2026-09-29

Phase 1 is implemented and remains PAPER-only.

### Implemented components
- `src/core/models.py`: opportunity, capital-limit, portfolio-state, decision models.
- `src/core/economics.py`: deterministic economic evaluation using `Decimal`.
- `src/core/risk.py`: deterministic hard-risk gate.
- `src/core/engine.py`: evaluation/risk/persistence orchestration.
- `src/persistence/sqlite.py`: local SQLite persistence for opportunities, decisions, ledger entries, and audit events.

### Economic rules
For each candidate:
- `expected_net = expected_revenue - expected_cost`
- `expected_value = p * (expected_revenue - expected_cost) + (1-p) * (failure_revenue - expected_cost - failure_cost)`
- `return_on_capital = expected_value / required_capital`
- risk-adjusted score is return on capital multiplied by confidence and liquidity score.
- A candidate is viable only when expected value and risk-adjusted score are positive.

All monetary calculations use decimal/fixed-point semantics.

### Risk rules
The deterministic governor rejects candidates when any hard check fails:
- required capital is not positive;
- allocation exceeds per-opportunity limit;
- allocation exceeds deployable capital after reserve;
- probability/confidence/liquidity is outside `[0,1]`;
- realized daily P&L has reached or breached the daily-loss limit;
- portfolio drawdown reaches or exceeds the maximum drawdown;
- opportunity leverage exceeds the configured leverage ceiling;
- expected value is not positive;
- evaluated risk-adjusted score is not positive.

Passing the gate produces `PAPER_TEST`. No Phase 1 path produces `EXECUTE`.

### Persistence
SQLite tables currently record:
- opportunities;
- risk decisions and hard-check results;
- ledger events;
- audit events.

Audit events link decisions to opportunity IDs and record the economic values used by the decision.

### Verification state
Restored baseline had 7 tests; current verification: 37 tests passed on Python 3.11.9.

### Current next step
Implement paper positions/orders, idempotency, daily simulation, and reconciliation before connecting any external opportunity source.

## Restoration and hardening — 2026-10-11

- Phase 1 source/config/docs/tests restored as normal tracked files, excluding bytecode.
- `expected_revenue` is conditional success revenue, not an already weighted estimate.
  `expected_cost` is paid in both outcomes (include fees/slippage/infrastructure here).
  Failure revenue defaults to zero; additional failure cost defaults to zero.
  Model lost principal explicitly in failure_cost when applicable; required_capital is
  a sizing input, not an automatic expense. Taxes remain caller-supplied assumptions.
- Inputs require finite Decimal values and valid ranges. Invalid construction raises
  ValueError before persistence; invalid data never produces paper approval.
- Risk recomputes economics and rejects mismatched supplied evaluations. Confidence
  and liquidity must yield a positive score in every invocation. Zero loss/drawdown
  budgets block all candidates; equality at a positive budget also blocks candidates.
- Opportunity IDs are immutable: duplicates raise IntegrityError and roll back the
  process transaction. Use a new ID for a revised observation. Outcome assumptions
  persist in an additive opportunity_outcomes table compatible with old databases.
- Opportunity, outcome assumptions, decision and audit writes are atomic.
  Repository writes participate in the caller's transaction (see OPERATIONS).
- PaperLedger rejects non-finite amounts; ledger event IDs remain unique.
- Live execution remains disabled; no credentials or external execution adapters added.

### Limits and next work
This is a candidate screening control plane, not a portfolio execution engine.
Callers supply allocated_today, realized P&L and equity; PAPER_TEST does not reserve
capital. Cross-day open exposure, order lifecycle, reconciliation, stale-source
checks, kill switch, and strategy calibration remain future work. Do not treat the
recording ledger as reconciled double-entry accounting. No live-readiness claim.

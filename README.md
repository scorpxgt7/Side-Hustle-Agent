# Side-Hustle Agent

Autonomous opportunity-discovery and capital-allocation platform designed to identify, evaluate, test, and eventually execute income-generating opportunities under deterministic risk controls.

## Current status

**Phase 1 — Bounded PAPER lifecycle, independent reconciliation and test-provider integration.** No live brokerage, exchange, payment-card, mining, or withdrawal execution is enabled.

## Non-negotiable operating rule

Before any code, configuration, schema, dependency, prompt, or infrastructure change:

1. Read `docs/PROJECT_CONTEXT.md`.
2. Read `docs/CHANGELOG.md`.
3. Read `docs/OPERATIONS.md`.
4. Read `docs/QA.md`.
5. Record the intended change before implementation when it changes architecture, capital controls, integrations, or security.
6. After implementation, update the context files with exactly what changed, why, verification performed, and remaining risks.

Never store debit-card numbers, CVV, bank passwords, private keys, seed phrases, or API secrets in this repository.

## Architecture

- Executive Orchestrator
- Opportunity Discovery Workers
- Economic Evaluation Engine
- Deterministic Risk Governor
- Paper Execution Layer
- Ledger / P&L
- Audit Log
- Human Approval Boundary
- Future adapter interfaces for brokers, exchanges, marketplaces, and compute providers

## Safety boundary

The agent may discover and simulate opportunities. Live financial execution remains disabled until a separate, explicitly configured execution profile exists and passes QA/security gates.

## Verification

```sh
python -m pip install pytest==8.3.5
python -m pytest
python -m compileall -q src
```

CI runs tests on Python 3.11 and 3.13. Source, configuration, docs, and tests
are restored from the Phase 1 archive; archives remain historical references.
See docs/PROJECT_CONTEXT.md for outcome semantics and remaining limitations.

Control arithmetic and outcome EV are exact and independent of caller Decimal
settings. Reporting ratios use a versioned 50-digit policy. Inputs are bounded
to 64 coefficient digits and exponents/magnitudes within [-128, 128]. Decision
audits include the effective limits, portfolio state, evaluation and decision ID.
The most recent dated PROJECT_CONTEXT entry supersedes historical phase labels.

Synthetic paper accounts can now persist cash and reserve cash/loss capacity
atomically through `PaperAccountService`. Identical request retries return the
stored result; conflicts and duplicate reservations fail closed. PHP and USD
profiles use exact cents, with no FX conversion. `PaperLifecycleService` supports
full fills, cancellation, explicit deadline expiry and full settlement. Balanced
journal records preserve cash, fees, cost-valued positions and P&L; open loss
capacity remains committed until closure. Known reservation databases migrate
transactionally to schema v3. See the latest OPERATIONS entry for the API and
supported cash-flow contracts. `PilotPaperService` adds immutable PHP vendor/budget
profiles, fresh evidence checks and journaled profit distributions. Reconciliation
independently reconstructs balances and commitments through a read-only connection.
Owner payouts reduce cash without counting distributed principal as a trading loss.
The default configuration still has zero capital and all execution flags false.

Run `python -m src.cli demo --db data/offline_demo.db` for the explicit offline
expense/income/payout fixture, or `python -m src.cli readiness` for activation blockers.
PayMongo adapters accept only test keys and documented transfer simulator numbers;
paid checkout events are not cleared income. No authenticated provider smoke test
has been run without account-owner test credentials. See
[the sandbox setup guide](docs/SANDBOX_SETUP.md) before using these APIs.

Verified expense-vendor integration, authoritative wallet/bank reconciliation,
market valuation, tax/refund reserves, repair/unhalt, actual source discovery,
daily orchestration, production hosting and reviewed live funding remain incomplete.

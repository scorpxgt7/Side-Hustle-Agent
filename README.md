# Side-Hustle Agent

Autonomous opportunity-discovery and capital-allocation platform designed to identify, evaluate, test, and eventually execute income-generating opportunities under deterministic risk controls.

## Current status

**Phase 1 — Candidate screening; paper execution and portfolio accounting remain incomplete.** No live brokerage, exchange, payment-card, mining, or withdrawal execution is enabled.

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

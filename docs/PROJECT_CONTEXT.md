# Project Context

## Mission
Evaluate lawful online income opportunities against risk-adjusted long-term net profit. No claim of profitability has been established.

## Current state
GitHub main contains two archived releases rather than checked-in source. This change adds a **reviewable Phase 1 reconstruction based on documented interfaces**, not a byte-for-byte extraction of the Phase 1 archive. Compare the archived source before relying on compatibility.

## Safety and architecture
- **PAPER ONLY**: no live brokerage, exchange, payment, withdrawal, or transfer integrations.
- LLM recommendations may not override deterministic risk controls.
- Never store card details, passwords, private keys, seed phrases, or API secrets.
- Components: opportunity model, economic evaluator, risk governor, SQLite opportunity/decision/audit persistence, and Phase1Engine.
- NO_ACTION / REJECT are valid; PAPER_TEST is neither an order nor permission to spend.

## Unimplemented safeguards
No portfolio cash reservations, idempotent order lifecycle, reconciled accounting, outcome provenance verification, external discovery feeds, dashboard, or daily scheduler. No real capital deployment is authorized.

## Next steps
1. Compare this reconstruction against the committed Phase 1 archive.
2. Add outcome evidence, provenance, and data freshness.
3. Add atomic paper capital reservation, order/fill simulation, idempotency, P&L, and reconciliation.
4. Only then add discovery and automation.

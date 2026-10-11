# OPERATIONS.md

## Mandatory pre-change procedure

Before modifying anything:

```text
READ PROJECT_CONTEXT.md
READ CHANGELOG.md
READ OPERATIONS.md
READ QA.md
CHECK CURRENT GIT/WORKTREE STATE
CHECK ACTIVE CAPITAL/EXECUTION MODE
THEN MODIFY
```

## Execution modes

### DISCOVERY
Find and normalize opportunities. No capital movement.

### PAPER
Simulate transactions with synthetic capital. No external monetary execution.

### APPROVAL
Prepare an executable action for human review. Do not execute automatically.

### LIVE
Reserved for a future release. Requires explicit enablement, isolated credentials, deterministic limits, monitoring, kill switch, reconciliation, and completed QA/security gates.

## Capital controls

Required controls for any future live profile:

- Maximum total deployable capital
- Maximum per-opportunity allocation
- Maximum daily loss
- Maximum aggregate drawdown
- Maximum leverage
- Maximum transaction value
- Maximum daily spend
- No withdrawal permission
- No credential creation without approval
- Automatic kill switch
- Reconciliation against authoritative account balances

## Incident response

Immediately disable execution if any of the following occurs:

- unexpected balance movement
- authentication anomaly
- repeated failed orders
- risk-limit calculation failure
- stale market data used for execution
- unexplained P&L discrepancy
- suspicious merchant/provider behavior
- corrupted or missing audit logs

Then preserve logs, switch to read-only/paper mode, and investigate before re-enabling.

## Phase 1 operating flow

```text
Opportunity
  -> EconomicEvaluator
  -> deterministic RiskGovernor
  -> PAPER_TEST or REJECT
  -> SQLite decision + audit event
```

`PAPER_TEST` is not permission to spend money. It means only that the candidate passed the current discovery-stage mathematical and capital checks.

The repository ledger is currently a recording mechanism; paper order/position accounting and reconciliation are the next implementation step.

## Restored build — 2026-10-11

Run the README verification commands before proposing changes. GitHub Actions runs
pytest and source compilation on Python 3.11 and 3.13 for pushes and PRs.
Keep config/default.yaml in PAPER with all execution capabilities false.

Supply explicit success/failure assumptions using the definitions in PROJECT_CONTEXT.
Use finite Decimal inputs; invalid inputs raise ValueError. Use a fresh opportunity
ID for each observation; retrying an existing ID fails without overwriting history.
Phase1Engine owns the atomic decision transaction. For direct Repository writes:

```python
with repository.conn:
    repository.ledger(event_id, "credit", amount)
```

Direct methods no longer commit individually. Commit related records together and
close the repository after use. Duplicate ledger IDs raise IntegrityError; never
silently reinterpret a conflicting event as success. Existing Phase 1 databases
receive the additive outcome table on opening; historical rows have no recorded
failure assumptions and must not be inferred as newly evaluated observations.

PortfolioState remains externally supplied and must include relevant allocation.
Screening does not move or reserve money. No daily runner or live mode is available.

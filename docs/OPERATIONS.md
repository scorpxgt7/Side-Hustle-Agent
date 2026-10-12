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

## Review correction procedures — 2026-10-12

Input Decimal values require at most 64 coefficient digits and an exponent and
adjusted magnitude within [-128, 128]. Do not silently quantize unsupported inputs.
Control/EV arithmetic is exact; ratio reporting uses exact-decimal-v1 (50 digits,
ROUND_HALF_EVEN, isolated exponent/trap settings). Changing caller Decimal context
must not change screening or paper balances. Paper updates validate both existing
balance and result before replacing the balance.

Use whitespace/control-free, nonempty string identifiers. Repository.ledger now
accepts only credit/debit and nonnegative magnitudes; zero remains valid. Event ID
uniqueness still rejects duplicates. These API checks do not make the recording
ledger a reconciled account or authorize arbitrary debits.

Repository.save_decision returns its integer row ID and rejects a mismatched
opportunity reference. Phase1Engine materializes the effective default state once
and stores full snapshots plus this ID in the same opportunity/decision/audit
transaction. Audit payload schema_version is 1, rule_version is phase1-screening-v2.
Older payloads lack these inputs; treat them as historical incomplete records.
No schema migration/backfill is required. Keep caller-managed direct transactions.

PHP 500 is the user-specified future cumulative pilot loss ceiling, not funded
capital. Funding integration and per-action policy are not implemented. Keep the
zero-capital PAPER default and all execution flags false.

## Synthetic account/reservation procedures — 2026-10-12

Use an isolated synthetic database/profile. The example below creates fictional
PHP cash, not real funds or an approved live budget:

```python
from decimal import Decimal as D
from src.persistence.sqlite import Repository
from src.core.models import CapitalLimits, Opportunity, OpportunityType
from src.core.paper_accounts import PaperAccountService, PaperActionSpec

repo = Repository("data/paper_demo.db")
service = PaperAccountService(repo)
limits = CapitalLimits(D("200"), D("0.5"), D("100"), D("200"), D("200"))
service.create_account("demo", limits, currency="PHP")  # Once; genesis is immutable.
opportunity = Opportunity("observation-001", OpportunityType.OTHER, "Synthetic fixture",
                          D("60"), D("120"), D("0"), D("0.8"), confidence=D("1"))
action = PaperActionSpec("PHP", D("60"), D("60"), "refundable")
result = service.submit("demo", "request-001", opportunity, action)
state = service.get_account("demo")  # Cash 200; reserved 60; available 40.
repo.close()
```

Keep request IDs stable across retries. Equivalent trailing-zero representations
are canonicalized. Conflicting payloads fail without replacing history; a rejected
request remains rejected on retry. Use a new observation ID for revised evidence.
Different accounts/currencies cannot reinterpret the same observation's denomination.
The action spec declares observation currency; verify it from source data before use.

Amounts must be exact PHP/USD cents. cash_required is the complete supported cash
commitment including fees. worst_case_loss must be at least that cash commitment.
Choose the capital treatment explicitly:
- refundable: expected_cost contains common fees/costs; failure_cost is principal
  write-off capped at required_capital; reserve required_capital + expected_cost.
- expensed: expected_cost already contains purchased principal; it must be at least
  required_capital; reserve expected_cost + failure_cost without adding principal again.

Unsupported obligations/fee schedules must not be squeezed into these contracts.
No fills or cash movement occur. RESERVED orders cannot yet be cancelled/expired;
release and settlement belong to the next reviewed lifecycle increment.

Service methods own Repository.transaction and must be called with no existing
transaction. This acquires BEGIN IMMEDIATE before account reads. Phase1Engine uses
the same owner and rejects nesting. For direct related writes, prefer:

```python
with repo.transaction():
    repo.ledger("event-001", "credit", D("1"))
```

Legacy `with repo.conn` direct-write usage remains compatible, but must not wrap
service or engine calls. No helper commits a caller's pending transaction. Default
busy timeout is 0.25 seconds; an explicit Repository timeout can be chosen. SQLITE_BUSY
raises OperationalError and returns no result. Retry the same key; do not invent a
new key after a lost response. Following an unknown outcome, reopen the repository
and submit the same payload/key to recover the committed result.

`service.halt(account_id, reason)` atomically persists a halt and audit; repetition
does not add effects. Reservations remain intact. get_account/submit perform limited
genesis/reservation checks and halt on a mismatch. No unhalt API exists until full
reconciliation supports it. Never manually patch balances to silence a discrepancy.

Before opening an existing database under this release, stop writers and take a
verified SQLite backup. New paper tables are added transactionally; legacy ledger
history is retained without inferred balances. Unknown versions/checksums or missing
tables fail closed. Do not delete metadata or recreate missing tables automatically.
After new writes, retain history and use a forward repair/compatible rollback rather
than discarding committed actions with an older snapshot.

Default config stays PAPER/zero capital with card/live/withdrawal flags false.
Source freshness, full accounting reconciliation and real-money funding remain absent.

## Paper lifecycle/accounting procedures — 2026-10-12

This section supersedes the reservation-only restrictions above. Use the same
isolated synthetic account and full observations; nothing here connects real funds.
PaperLifecycleService also provides create_account, submit, get_account and halt.

```python
from decimal import Decimal as D
from datetime import datetime, timedelta, timezone
from src.core.paper_lifecycle import (
    PaperLifecycleService, PaperFillSpec, PaperSettlementSpec,
)

service = PaperLifecycleService(repo)  # Existing synthetic demo account/repository.
reserved = service.submit(
    "demo", "request-002", new_opportunity, new_action,
    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
)
if reserved.status == "RESERVED":
    order = service.get_order("demo", reserved.order_id)
    filled = service.fill(
        "demo", "fill-002", order.order_id,
        PaperFillSpec("PHP", D("60"), D("5")),  # Full principal plus entry fees.
        expected_version=order.version,
    )
    if filled.status == "FILLED":
        settled = service.settle(
            "demo", "settle-002", order.order_id,
            PaperSettlementSpec("PHP", D("60"), D("20"), D("2")),
            expected_version=filled.order_version,
        )
```

This example requires an all-in cash reservation of at least 65. With refundable
principal, settlement returns 60 principal plus 20 revenue less 2 exit fees; total
position profit is 13 after 5 entry fees. Expensed purchases instead use zero
capital_return and put all gross sale receipts in revenue. A zero-return/zero-revenue
settlement records the supported write-off. Supply actual simulated costs/receipts,
not forecasts relabeled as realized income. Exit fees cannot exceed gross receipts.

Cancel an unfilled reservation with service.cancel(account_id, event_key, order_id,
expected_version=0). Expire it using the same signature with service.expire after its
persisted deadline. Neither spends cash nor creates income. A cancellation/expiry
cannot reverse a fill. No partial fills/settlements or post-close edits are supported.

After any uncertain outcome, reconnect and repeat the identical command/key/version.
Accepted and rejected responses are durable. A stale-version rejection requires a
fresh state read and a new intentional command/key; changing a cached key's payload
raises RequestConflictError. Retrying submit after cancellation still returns its
historical RESERVED receipt: get_order is the current status. Do not treat historical
receipts as authorization. A reviewed new experiment needs a fresh observation ID.

Deadlines require aware datetime inputs and are normalized to UTC. The service owns
its clock (injectable only for deterministic simulation/testing); expiry does not take
an arbitrary per-command timestamp. There is no expiry scheduler or automatic release.
Cash/deployed/open-loss/P&L/equity are exposed by get_account; get_order exposes order
version, deadline, position status, spent cash, open loss and closed total P&L. Equity
uses cost-valued refundable assets; no unrealized market P&L is claimed.

All lifecycle methods own a transaction and must not be wrapped in one. Lock errors
and SQL/audit failures produce no successful response and no partial money movement.
Audit/receipt data and journals commit together. Internal discrepancies halt movements
while retaining commitments; never patch tables to force agreement. An intact halted
account permits cancellation/expiry/full settlement but stays halted. No unhalt exists.

Stop writers and take/verify a SQLite backup before the v2 upgrade. Opening a known,
intact v1 database rebuilds order/reservation tables, preserves identities and adds
positions, lifecycle receipts and sealed journals transactionally. Genesis postings
link to existing explicit synthetic genesis audits. Unsupported/incomplete schemas
or inconsistent history fail closed; failed migration leaves v1 paper data/schema
intact. Do not reopen a v2 database with v1 code or delete metadata/history. Keep a
forward repair path; never restore an old snapshot to erase committed actions.

Journal/lifecycle UPDATE/DELETE operations are blocked. The legacy ledger remains
historical and separate. Current internal checks do not provide market valuation,
external reconciliation or repair authorization. P&L has no daily reset and filled
positions retain risk across dates. Live funding, PHP 500 pilot activation, card access
and external execution remain unavailable; default config stays PAPER/zero capital.

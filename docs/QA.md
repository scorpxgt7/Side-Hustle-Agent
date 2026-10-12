# QA.md

## Release gates

A change cannot progress toward live execution unless all applicable gates pass.

### Domain correctness
- Monetary values use decimal/fixed-point semantics rather than binary floating point.
- Fees and slippage are included in economic calculations.
- Negative expected value is rejected.
- `NO_ACTION` is a valid outcome.

### Risk
- Risk limits are deterministic.
- Model output cannot override hard limits.
- Position sizing is bounded.
- Drawdown and daily-loss checks are tested.
- Kill switch behavior is tested.

### Security
- Secrets are never committed.
- Payment-card data is never stored.
- API credentials use least privilege.
- Withdrawal capability is disabled.
- Logs do not contain credentials or sensitive payment data.

### Reliability
- Failed external calls fail closed for execution.
- Stale data is detected.
- Duplicate execution is prevented with idempotency keys.
- Ledger reconciliation detects discrepancies.

### Auditability
- Every decision has an opportunity ID and decision ID.
- Every paper/live action has an audit event.
- Changes to limits are recorded.
- Context files are updated after relevant changes.

## Phase 1 acceptance criteria â€” 2026-09-29

- [x] Monetary calculations use `Decimal`.
- [x] Expected revenue and expected costs are included in evaluation.
- [x] Negative expected value is rejected.
- [x] Risk-adjusted score cannot convert a negative-EV opportunity into an approved candidate.
- [x] Per-opportunity allocation and aggregate deployable-capital limits are enforced.
- [x] Daily-loss breach is rejected.
- [x] Drawdown breach is rejected.
- [x] Invalid probability/confidence/liquidity values are rejected.
- [x] Phase 1 cannot emit a live execution decision.
- [x] Opportunities, decisions, and audit events persist to SQLite.
- [x] Full automated suite passes.

### Phase 1 verification command

```bash
pytest -q
```

Historical baseline result: `7 passed`. Current verification appears below.

## Restoration verification — 2026-10-11

Verified locally with Python 3.11.9 and pytest 8.3.5: **37 passed**.
`python -m compileall -q src` also passed. CI covers Python 3.11 and 3.13;
remote CI status must be checked on the PR independently.

Added regression coverage:
- Sunk costs, failure losses/salvage and probabilities zero and one.
- NaN, infinities, floats, invalid limit/state inputs.
- Exact loss/drawdown boundaries, aggregate allocation and leverage.
- Zero-confidence rejection and tampered evaluation rejection.
- Injected audit failure rolls back all decision records.
- Duplicate opportunity IDs preserve prior history and decision count.
- Outcome assumptions persist; duplicate ledger events fail.
- Paper insufficient funds/non-finite values and disabled execution config.

Reliability gates above for stale data, kill switch and reconciliation are future
live-release gates, not implemented Phase 1 capabilities.

## PR #2 review regression verification — 2026-10-12

Local Python 3.12.14 / pytest 8.3.5: **71 passed**. Source compilation and
git diff whitespace checks passed. Required CI remains Python 3.11 and 3.13;
check both jobs on the latest PR head before merge.

New regression coverage:
- Zero loss budget with negative, zero and positive P&L; persisted rejection.
- Positive daily-loss equality/breach, exact drawdown equality and capital sums
  whose small increments disappear at ordinary Decimal precision.
- Altered caller precision, rounding, exponent limits and Inexact/Rounded traps;
  EV, controls, ratios and balances remain invariant without caller signal flags.
- Small positive scores at supported size extremes remain positive.
- Unsupported numeric sizes reject before writes/mutation; result overflow leaves
  the prior paper balance intact.
- Invalid opportunity/event IDs and negative/unknown ledger operations do not
  persist; invalid writes roll back related caller-transaction records.
- Decision opportunity reference mismatch is rejected.
- Full audit input snapshots reconstruct accepted/rejected decisions after reopen,
  including the effective default state, row ID and rule/arithmetic policy versions.

Earlier aggregate-capital checkboxes cover caller-supplied screening snapshots,
not reservations, cross-day exposure or concurrent allocation. Those capabilities,
paper order idempotency, reconciliation, freshness, kill switch and live gates
remain unimplemented. PHP 500 has not been funded or activated as a runtime limit.

## Paper account/reservation verification — 2026-10-12

Local Python 3.12.14 / pytest 8.3.5: **115 passed**. Source compilation and git diff
whitespace checks passed. Verify Python 3.11/3.13 CI on the follow-up PR's exact head.

Acceptance coverage:
- Synthetic genesis is immutable and audited; failures create no account.
- Independent connections competing for 100 deployable cash with two 60 requests
  produce one reservation; concurrent identical requests return one durable effect.
- Numeric-equivalent retries return stored accepted/rejected results, while conflicting
  request/observation identities and duplicate accepted observations cannot add orders.
- Cash/fees, capital-cost treatment, single allocation and conservative aggregate loss
  checks use effective persisted limits; zero/equality stops reject prospective exposure.
- Explicit currency and per-observation denomination prevent implicit FX reinterpretation.
- SQL failures at request/order/reservation writes and audit failures roll back all
  related observation/decision/account records. No cash is debited by reservation.
- Real process exit before commit recovers uncommitted state; exit after commit before
  response permits exact same-key recovery without a second decision/order/reservation.
- Nested transactions cannot commit caller records; busy writers return no approval and
  the unchanged request key can be retried after lock release.
- Reservations survive reopening and old timestamps; no date-based capital release.
- Persistent/idempotent halts block new capacity; halt-audit failures preserve prior state.
- Projection and malformed-genesis discrepancies halt accounts; this is a reservation-only
  consistency check, not completed journal, position, market or external reconciliation.
- Foreign keys reject cross-account reservation links; legacy ledger data is not imported
  as cash. Unknown/incomplete/unversioned schemas fail before silent reconstruction.
- SQLite backup restoration preserves cash/reservations and request identities.
- Unsupported scales, storage bounds, currencies, loss assumptions and LIVE mode reject.

Remaining gates: fill/cancel/expire/settle transitions, positions, balanced journal,
valuation/full reconciliation/unhalt, freshness contracts, daily runner and live release.
No debit-card integration, real deposit, purchase, trade or realized profit is tested here.

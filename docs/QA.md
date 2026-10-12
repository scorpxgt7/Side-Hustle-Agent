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

## Paper lifecycle/accounting verification — 2026-10-12

Acceptance criteria for this increment:
- Full refundable fills transfer principal to cost-valued assets, realize fees once,
  release unused cash encumbrance and retain open conservative loss capacity.
- Expensed purchases realize entry spend once; subsequent receipts do not expense
  principal again. Profit, full loss, salvage and break-even flows have independent
  expected cash, equity, position P&L and aggregate realized P&L assertions.
- Open positions continue to consume allocation/loss/drawdown capacity; aggregate
  all-in deployment remains capped at genesis policy even after profits.
- Cancellation/explicit expiry release only unfilled commitments exactly once and
  produce no income. Expiry requires a persisted deadline; equality blocks fills.
  Cached rejections remain rejected after time advances; no automatic day release.
- Same-key retries recover historical responses across reopen, closure and backup;
  changed command payloads conflict, stale versions reject and cross-account links fail.
- Independent connections racing fill/cancel, identical fill, and same/different-key
  settlement produce one effect with consistent projections.
- SQL failures at reservation deletion, position/order/account changes, journals,
  postings and event receipts roll back the entire fill/settlement. Audit failures
  roll back each transition. Busy locks/nesting cannot commit caller work.
- Real process exit after writes before commit restores reservations/cash; exit after
  commit before response permits same-key recovery without duplicated movement.
- Journals balance in cents, survive backup/reopen and reject history rewrites.
  Extra postings, altered position/audit/deadline records halt before more movement.
- Halted intact accounts block fills but permit supported exposure reduction while
  remaining halted. Corrupted accounts cannot cancel away unexplained commitments.
- Pinned actual v1 schema migrates preserving reservations/request responses/audits,
  backfills only explicit synthetic genesis and verifies foreign keys. Bad cash,
  reservations, requests and foreign keys abort all paper DDL/backfill. Missing history
  guards and unknown schemas fail closed.
- Invalid scale/non-finite/negative money, storage overflow, currency mismatch,
  partial principal and overspend reject. Caller Decimal context cannot change money.

Remaining gates: standalone reconciliation/reporting and reviewed repair/unhalt,
market valuation, day boundaries/spend accounting, source freshness, daily runner,
strategy evidence and separately reviewed live-release controls. Synthetic outcomes
are not evidence of an available profitable strategy or real-money readiness.

Verification result for this increment: **185 passed** locally on Python 3.12.14
with pytest 8.3.5. Source compilation and git diff whitespace checks passed.
Python 3.11/3.13 remain required CI; verify both against the published PR head.

## Reconciliation/autonomy sandbox verification — 2026-10-12

Local Python 3.12.14 / pytest 8.3.5: **249 passed**. Source compilation and diff
whitespace checks passed. The offline CLI expense/income/profit-distribution cycle
and independent read-only report executed successfully. Verify required Python
3.11/3.13 CI against the published PR head before merge.

New acceptance coverage:
- Independent reconstruction does not invoke writer projection code; discrepancies
  report exact fields without modifying the database or creating a missing database.
- Expected cash/P&L/performance equity, distributed owner profit, open commitments,
  retained fee holds and payout drawdown distinguish distributions from expenses.
- Approved vendors, immutable profiles/routes, fresh/not-future evidence, one-off
  contracts and lifetime spend/loss limits reject before additional approvals. Gains
  cannot reset caps. An expired declaration prevents spending an existing reservation.
- Profit-only payouts reject open/halted accounts, protected cash use, changed routes,
  insufficient net profit and excess fees. Unknown/unverified outcomes retain holds.
- Same-key results survive later state changes; concurrent connections cannot double
  reserve profit or produce a second provider POST. Provider fingerprints bind jobs.
- Audit failures roll back payout requests/claims/settlement; failed provider claims
  never call HTTP. A crash after a durable dispatch marker recovers by GET only.
- Old intact v1/v2 databases migrate while preserving history; inconsistent v2 data
  leaves schema/checksum/guards/history intact. Foreign keys remain valid.
- Test provider endpoint payloads use v2 creation and documented read paths, integer
  PHP amounts, stable idempotency/ref values and simulator GoTyme/PESONet targets.
- Live/missing/invalid keys, non-test destinations/resources, mismatched currencies,
  identities, fee overruns, refunds/disputes and malformed money fail closed.
- Normalized receipts omit provider billing/card objects. API errors and readiness
  output do not expose secret keys/source wallet numbers.
- HMAC test webhooks reject wrong raw bodies, live modes/signatures, duplicate fields
  and stale/future timestamps. Authentication alone never credits available money.
- Paid network checkout remains PAID_UNCLEARED, even with past scheduled timestamps;
  fixture clearing cannot be invoked for a network sandbox adapter.
- Interrupted/unknown creation is never resubmitted. Empty/ambiguous lookup cannot
  release holds; confirmed recovery retains the account's halt.
- Repeating the offline end-to-end demo adds no duplicate expense/income/distribution.
  Its outputs are explicitly synthetic, with real_money_moved=false.

Validation limit: HTTP transports and provider responses were mocked; no test key
or test wallet is installed, so authenticated provider sandbox smoke tests have not
run. Passing mocks does not establish merchant capability, live eligibility or profits.
Outstanding live gates include authoritative wallet/bank clearing, verified one-off
expense vendor and own-bank route, credentials/onboarding, deployment/webhook security,
tax/refund/obligation reserves, market/source truth, strategy evidence, day/scheduler
controls, reviewed repair/unhalt and separately reviewed activation. No real card
charge, customer income, deposit or payout occurred.

### Final review regression verification — 2026-10-12

Latest local result: **253 passed** on Python 3.12.14 / pytest 8.3.5. Source compilation
and diff whitespace checks passed. Additional tests prove that older submission,
settlement and payout entry points cannot bypass persisted lifetime-spend/fee/route
policies; corrupt pilot audits are reported independently and halt through the older
account API. Check Python 3.11/3.13 CI on the updated head, not the earlier 249-test head.

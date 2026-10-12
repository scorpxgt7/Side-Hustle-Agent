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

## Current state and review corrections — 2026-10-12

This dated entry is authoritative for current behavior and next work. Earlier
phase/status/next-step sections remain historical records. Current phase is
**Phase 1 candidate screening; paper execution and portfolio accounting incomplete**.

- Zero daily-loss budgets reject every candidate, including after realized gains.
  Equality at positive daily-loss/drawdown limits also rejects candidates.
- Outcome EV, net, deployable capital, drawdown, allocation sums and paper balance
  changes are exact. Decimal inputs/outputs use exact Fraction intermediates;
  no binary floats or ambient Decimal arithmetic determine these values.
- Inputs accept at most 64 coefficient digits; stored exponent and adjusted
  magnitude must lie in [-128, 128]. Unsupported sizes raise ValueError.
- Return on capital and risk-adjusted score are computed from exact ratios and
  rendered independently at 50 significant digits, ROUND_HALF_EVEN, under the
  versioned exact-decimal-v1 policy. Caller precision, rounding and traps do not
  affect results. The input bounds keep supported positive scores representable.
- IDs must be nonempty strings without whitespace/control characters. Model and
  repository boundaries reject invalid opportunity/event identifiers before writes.
- Recording-ledger operations accept only credit/debit with nonnegative bounded
  Decimal magnitudes. This still does not reconcile cash or enforce debit solvency.
- Each new decision audit stores its decision-row ID, full effective opportunity,
  CapitalLimits, PortfolioState, evaluation, checks and arithmetic/risk-rule version.
  Historical audits are retained without fabricated input backfills.
- There is no database schema migration for these fixes: audit snapshots use the
  existing JSON payload. Direct save_decision now returns the inserted integer ID.

The user has selected PHP 500 as the cumulative maximum loss for a future first
live pilot and a real physical debit card dedicated to testing as a potential
funding source. This is not a deposit, a daily allowance or payment approval.
No issuer/provider or actual capital amount is selected. Default synthetic config
remains USD with zero capital; do not interpret PHP 500 as a USD configuration value.

Next: persisted paper-account state and atomic reservations, then order/position
lifecycle, reconciliation and freshness controls under the documented release gates.
Live execution, card access and withdrawals remain disabled.

## Current state — paper account/reservation increment, 2026-10-12

This entry supersedes previous current-state and next-step entries. Phase 1 now
includes candidate screening plus **synthetic account genesis and atomic paper
reservations**. Full paper execution/accounting and real execution remain incomplete.

- PaperAccountService creates an explicit synthetic account from immutable
  CapitalLimits. It does not import the recording ledger or accept a bank/card
  deposit. Cash and equity remain at genesis until a later lifecycle release.
- PHP/USD monetary inputs to this service require exact cents within signed
  64-bit storage. General candidate screening retains its documented Decimal bounds.
  Protected reserve is rounded upward to a cent; available cash never consumes it.
- Account mode is PAPER, leverage ceiling must be 1, and each observation has an
  immutable declared currency. No FX conversion, borrowing, shorting or live adapter.
- Requests have identities separate from observation IDs. Identical numeric/payload
  retries return the original persisted result, including rejections. Conflicts
  raise RequestConflictError. A second key cannot reserve an accepted observation
  again in the same account. Changed observations require fresh observation IDs.
- Submit reloads account state after acquiring BEGIN IMMEDIATE. Cash, per-action
  limits, aggregate reserved loss and prospective daily-loss/drawdown boundaries
  are checked before related decision/order/reservation/account/audit writes commit.
- PaperActionSpec requires explicit currency, all-in cash_required, worst_case_loss
  and capital_treatment. Initial policy reserves at least full cash as potential loss.
  Refundable contract: principal plus common costs must be cash-covered; failure_cost
  represents principal write-off only and cannot exceed principal. Expensed contract:
  expected_cost includes principal; cash covers expected_cost plus failure_cost.
  Other cash-flow semantics require a later supported contract.
- Orders are RESERVED only. Reservations encumber cash without debiting it; they
  and their loss allowance survive restart/day changes. There is no daily reset,
  fill, cancel, expiry, position, settlement, income or profit generated by this slice.
- Explicit halts survive restart and return zero available capacity for new actions.
  A limited genesis/reservation projection check can also halt on discrepancies.
  Full journal reconciliation/valuation and safe unhalting remain future work.
- Schema version/checksum guard prevents opening unknown, incomplete or unversioned
  paper schemas for writes. Legacy observations missing outcome assumptions cannot
  be used as newly complete paper observations. Existing records remain unchanged.
- Transaction nesting is rejected. Busy lock errors produce no approval; callers
  retry the same stable key after reconnecting/reloading. An identical stored result
  is historical, not a new execution authorization.

PHP 500 remains the cumulative loss ceiling selected for a future live pilot;
it is not funded, activated, converted to USD or reset as a daily allowance here.
All configuration execution flags remain false. Next required increment: paper
fill/cancel/expire/settle lifecycle with balanced accounting, then full reconciliation,
source freshness and daily orchestration before separate live-release gates.

## Current state — paper lifecycle/accounting increment, 2026-10-12

This entry supersedes earlier current-state/next-step entries. Phase 1 now supports
**synthetic reservations and full order/position lifecycle with balanced accounting**.
It remains a restricted PAPER simulation; no provider or real-money action exists.

- PaperLifecycleService extends account/reservation APIs with fill, cancel, expire,
  settle and get_order. RESERVED becomes FILLED, CANCELLED or EXPIRED; FILLED opens
  one position and settlement closes it and the order as CLOSED. Only full fills at
  the immutable observation's principal and one full settlement are supported.
- Every lifecycle command requires a stable account-scoped event key and expected
  order version. Equivalent retries recover the original accepted/rejected response;
  changed inputs conflict. Historical results do not authorize another transition.
  BEGIN IMMEDIATE serializes effective state reads, journal/account/order writes,
  event responses and audit snapshots. Fill/cancel and settlement races have one effect.
- A fill spends principal plus explicit actual entry fees within the cash reservation.
  Unused cash encumbrance is released. Deployment and residual conservative loss
  remain assigned to the open position; reservations alone no longer represent risk.
  New submissions count reserved plus deployed all-in cash against the immutable
  genesis deployment ceiling. Profits cannot silently increase that ceiling.
- Refundable principal moves from cash to a cost-valued asset; entry fees reduce P&L.
  Expensed principal plus entry fees reduce P&L immediately and create no asset.
  Remaining position loss is original loss allowance minus expense already realized.
  Loss/drawdown checks include remaining open risk and realized expenses exactly once.
- Settlement explicitly separates capital_return, revenue and exit fees. Refundable
  principal return is capped at original principal; missing return becomes a write-off.
  Expensed purchases require zero capital_return, with sale receipts in revenue.
  Exit fees must be deducted from gross proceeds; additional outgoing obligations,
  partial fills/settlements, borrowing, shorting, FX and unsupported contracts reject.
  Total position P&L is all net proceeds minus entry cash spent; costs are not charged twice.
- Orders may receive an immutable aware-UTC expiry deadline on submit. The service
  clock must reach it before explicit expiry. Late fills reject while commitments
  remain held. Old dates or clock advancement never release cash automatically.
  Migrated orders without a deadline retain commitments until explicit cancellation.
- Journal entries/postings represent synthetic genesis equity, deployment, expenses,
  income and settlement. Entries are balanced in integer cents and linked to audits.
  Journal and lifecycle history reject UPDATE/DELETE; sealed posting manifests detect
  extra/changed postings. The older recording ledger is independent and never funds cash.
- Internal checks compare journal cash/assets/P&L/peak, reservations, positions, command
  receipts, currency, order versions and audits to projections. Discrepancies persist a
  halt and retain commitments. Halted accounts block deployment; intact cancellation,
  expiry and supported settlement can reduce exposure while the halt remains in place.
  No unhalt/repair API, market marks or authoritative external reconciliation exists.
- Schema v2 preserves the pinned v1 checksum. Known intact v1 databases upgrade under
  one transaction, preserving orders, requests and historical audit records. Genesis
  journals derive solely from explicit synthetic funding audits. Inconsistency, foreign
  key problems or unsupported schemas abort; no legacy funds or profits are inferred.

The historical realized_daily_pnl_minor field currently holds cumulative realized P&L;
there is no day boundary/reset, daily-spend accounting or automatic runner. Equity uses
cost for refundable positions, not market value. Next smallest required increment is
a read-only reconciliation report that independently reconstructs account balances,
open commitments and audit-linked history, reports discrepancies and keeps writes
halted; then define valuation/day boundaries and reviewed repair/unhalt. Source
freshness and daily orchestration still precede separate live-release gates.

PHP 500 remains the cumulative maximum loss for a future live pilot, without funding,
card access, live activation or a configured pilot profile. All execution flags stay false.

## Current state — reconciliation and sandbox autonomy, 2026-10-12

This entry supersedes earlier current-state and next-step entries. The user clarified
that the intended funding method is a **GoTyme Bank Visa virtual debit card** and
requested the implementation/integrations needed for autonomous bounded expenses
and income payouts. This updates the earlier physical-card assumption. No card,
account number, CVV, PIN, OTP, bank login or provider secret has been collected.
The PHP 500 cumulative maximum loss remains a future real-pilot requirement, not
a deposit amount or permission for an arbitrary charge.

Implemented in this increment:
- Independent read-only reconstruction of audited genesis, commands, journal manifests,
  cash, P&L, peak/performance equity, open exposure, reservations, payout holds and
  provider outbox history. reconcile_path opens mode=ro with a consistent snapshot;
  it cannot migrate/create, repair balances or unhalt. Scope is INTERNAL_PAPER_ONLY.
- PilotPaperService binds immutable PAPER/PHP profiles before experiments. The example
  policy uses PHP 50/action, PHP 500 lifetime spending and PHP 500 cumulative loss;
  these are simulation defaults, not chosen live purchasing permissions. Gains do
  not reset lifetime spending or increase policy limits. Fees/remaining commitments
  are counted. Only approved vendor_ references, expensed one-off contracts and
  fresh aware-UTC HTTPS evidence declarations pass automation. Declarations are not
  source verification or evidence of actual demand/profitability.
- Profit-only synthetic payouts use one opaque route_ reference. All experiments must
  be closed; protected/genesis capital, prior distributions, fee caps and pilot budget
  checks apply. RESERVED -> SENDING -> PENDING/UNKNOWN/SUCCEEDED/FAILED is audited;
  unknown states retain all holds and halt the account. Only matching confirmed
  terminal outcomes release capacity. Halts remain until a future reviewed unhalt.
- Successful payout journals debit owner equity and fee expense, crediting cash.
  Current/peak equity now measure performance including distributed profits, so owner
  distributions are not trading drawdown; payout fees remain actual expense. With no
  distributions this is identical to the earlier cost-valued equity behavior.
- PayMongo test-key-only adapters cover current v2 checkout/transfer writes and their
  documented read routes. Live keys/resources, redirects, other destinations/currencies,
  mismatched correlations/fees and malformed amounts reject. Only published test
  transfer numbers are accepted; no actual GoTyme account is used in simulation.
- Durable provider claims/dispatch markers commit before HTTP. No network call occurs
  inside a database transaction. Same-key retries/restarts cannot repeat an attempted
  POST. Unknown payout recovery uses read-by-reference and does not rely on provider
  idempotency retention past its documented approximate 24-hour window. Credential,
  source/destination scope is fingerprinted and cannot change on an existing operation.
- Customer hosted checkout collects payment through PayMongo, not this repository.
  Raw billing/card objects are not persisted. PAID_UNCLEARED never credits cash or
  authorizes payouts; scheduled available/credited timestamps are not clearance proof.
  Only the explicitly offline fixture path simulates clearing for the end-to-end demo.
- Test webhook verification uses documented raw-body HMAC-SHA256, mode checks,
  constant-time comparison, timestamp tolerance and size limits. No HTTP webhook
  service has been deployed or provider account configured by this release.
- Schema v3 preserves pinned v1/v2 checksums and upgrades known intact schemas under
  one transaction without changing existing journal IDs/amounts/audits. New profile,
  payout and sandbox outbox history remains audited and guarded against rewrites.
- CLI provides readiness, independent reconcile, offline demo, and explicitly selected
  sandbox checkout/payout/read operations. The fixture simulates 50 expense, 100
  income less 5 deductions, then 35 distribution plus 10 payout fee; closing cash is
  500 with 35 distributed. It represents no real customer, return or profit evidence.

Provider capability findings are sourced in SANDBOX_SETUP.md. PayMongo's published
bank table lists GoTyme for PESONet, not InstaPay at review time; merchant/account
eligibility still needs authenticated verification. Deprecated wallet-v1 transfer
writes are not used. No direct GoTyme consumer card-control API or verified expense
merchant purchasing adapter has been established. Card payment acceptance is not
an API for buying anything on the web, and merchant checkout must not be used to
self-charge funds into an experiment wallet.

Verification is offline/mocked: no PayMongo test key or test wallet is configured in
this session, so authenticated provider sandbox smoke tests have not executed. No
real deposit, charge, customer receipt, payout or card access occurred. Default
PAPER/USD/zero capital and all execution flags stay false; the PHP profile is explicit
synthetic data, not a conversion of that default or an activated live pilot.

Next required integration: account-owner provider onboarding/private test credentials
and authenticated contract checks, followed by authoritative cleared-wallet/bank
reconciliation and a verified one-off expense vendor/secure saved-card path. Before
live release also implement secure hosting/auth/webhook processing/monitoring,
verified own-bank destination, tax/refund/obligation reserves, strategy evidence,
discovery/daily orchestration, and reviewed release/unhalt controls. No real-money
readiness or guaranteed daily income is claimed.

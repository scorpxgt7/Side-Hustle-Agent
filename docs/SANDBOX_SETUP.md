# Side-Hustle Agent sandbox setup

This release provides a runnable **offline PAPER cycle**, independent reconciliation,
and PayMongo **test-key-only** API adapters. It does not charge your GoTyme card,
create real customer income or transfer real profits. The intended future expense
method is the user's GoTyme Visa virtual debit card; the intended receiving bank is
GoTyme. No card number, CVV, PIN, OTP, bank login or personal account number is needed
for any command in this guide.

## Run the complete offline flow

From the repository root with Python 3.11 or 3.13:

```sh
python -m src.cli readiness
python -m src.cli demo --db data/offline_demo.db
python -m src.cli reconcile --db data/offline_demo.db --account demo
```

The deterministic fixture creates PHP 500 **synthetic** capital, spends 45 plus 5
entry costs, simulates a 100 customer receipt with 5 deductions, and distributes
35 of recorded profit plus a 10 payout fee. Closing cash is 500; performance equity
is 535, including 35 already distributed. All amounts are fictional. Repeating the
same demo/database adds no second spend, receipt or payout. `real_money_moved` is
false; fake checkout URLs in internal records must never be used to enter a card.
The fixture uses an explicit fixed clock, not current or verified market evidence.

`reconcile` opens SQLite read-only and never creates/migrates a database, repairs
records or releases a halt. Exit 0 means internal records match; exit 2 means a
reported discrepancy/block. It is not proof that money exists at any provider.

## Connect a real provider sandbox

1. Create/activate your PayMongo account on its official dashboard and complete the
   verification required for checkout and wallet test capabilities. This is an
   account-owner action; this repository has not registered an account for you.
2. Put the **test** secret key in the backend's private environment as
   `PAYMONGO_TEST_SECRET_KEY`. It must start with `sk_test_`. The code rejects live
   keys before database changes or HTTP requests. Never send keys through chat or
   commit them. `.env.example` contains variable names only and `.env` is ignored.
3. For transfer tests, configure `PAYMONGO_TEST_WALLET_NUMBER` using the test wallet
   source returned by your provider dashboard/API. This is not your GoTyme account
   or virtual card number. Wallet/source eligibility still needs provider validation.
4. `PAYMONGO_SIMULATOR_DESTINATION` can be one of the provider's documented numbers:
   `999999990001` succeeds; `999999990002` through `999999990006` simulate failures.
   The client rejects all other destinations. Never use your real bank account or card.
5. Run authenticated sandbox smoke checks and compare the results to the provider's
   dashboard. No test key/wallet is configured in the development session used for
   this release, so no authenticated provider API requests were executed there.

To prepare a filled synthetic experiment, use a fresh database:

```sh
python -m src.cli demo --db data/network_sandbox.db --stop-after-fill
```

Copy the opaque `order_id` from that output. After privately configuring the test
key, replace `ORDER_ID_FROM_OUTPUT` below with it:

```sh
python -m src.cli sandbox-checkout --db data/network_sandbox.db --account demo --order ORDER_ID_FROM_OUTPUT --request-key checkout-001 --amount 100
```

This creates a **customer test checkout**. It does not save the user's GoTyme card
for arbitrary spending, and must not be used to charge yourself to fund experiments.
Only provider-supplied test payment details belong on a test checkout page.

Copy the opaque operation `id` from the command output, then read its status:

```sh
python -m src.cli sandbox-poll-checkout --db data/network_sandbox.db --account demo --operation-id OPERATION_ID_FROM_OUTPUT
```

A confirmed paid test checkout becomes `PAID_UNCLEARED`. It does not close the paper
position, credit available paper cash or authorize a payout. Scheduled `available_at`
or `credited_at` timestamps alone are not proof of a cleared available wallet balance.
Only the explicitly offline fixture path simulates clearance in this release.
Authoritative wallet/settlement reconciliation is the next integration gate.

The API layer uses POST `/v2/checkout_sessions` and GET
`/v1/checkout_sessions/{id}`. Raw provider billing/card objects are normalized to
opaque IDs and integer amounts before persistence. There is no card collection form.

## Transfer test behavior

A synthetic payout must first pass the recorded-profit, closed-position, fixed-route,
fee and lifetime-policy checks in `PilotPaperService.request_payout`. Its opaque
`payout_id` can then be passed to the gateway:

```sh
python -m src.cli sandbox-payout --db data/transfer_sandbox.db --account demo --payout-id PAYOUT_ID_FROM_PAPER_SERVICE
python -m src.cli sandbox-poll-payout --db data/transfer_sandbox.db --account demo --payout-id PAYOUT_ID_FROM_PAPER_SERVICE
```

The database must contain that synthetic payout. This command does not create a
payout based on a CLI amount or accept a new destination. Configure test credentials
before a new transfer attempt. The adapter uses `/v2/batch_transfers`, the persisted
payout ID as `Idempotency-Key`/`reference_number`, and a read lookup through
`GET /v2/transfers?reference_number=...`. It does not use deprecated wallet-v1 writes.
Its request shape follows the provider's published v2 example; authenticated contract
smoke tests are still required to verify the enabled merchant wallet and bank route.

A durable dispatch marker is committed before POST. Concurrent calls and restarts
cannot send a second POST. SENDING is retained after a crash; use the read-only poll
command. UNKNOWN/PENDING never release cash. Unknown outcomes halt the account.
Reconciliation can confirm a matching terminal outcome; it does not automatically
unhalt. Confirmed fee overruns, missing/mismatched IDs, other currencies, other
recipients, ambiguous reads or any `livemode` other than false remain blocked.

PayMongo retains provider idempotency responses for about 24 hours. This code never
relies on indefinite provider deduplication: it does not resend an uncertain POST,
even after the provider key might expire. An empty lookup is not permission to retry.
Provider fees are reserved before dispatch and must fit the stored cap.

## Webhooks and deployment

`verify_test_webhook` implements the documented raw-body HMAC-SHA256 `te` signature,
constant-time comparison, test-mode checks, bounded size and timestamp tolerance.
It is a parser, not a deployed HTTP endpoint. Verified paid notifications alone must
not book cash or trigger payouts. A hosted authenticated service, HTTPS endpoint,
durable event processing, alerting and authoritative wallet/bank reconciliation are
required before deploying a real integration. API error responses never log raw
provider bodies or credentials.

## Current pilot boundaries

- Profiles are PAPER/PHP only. The PHP 500 cumulative-loss choice remains the user
  requirement; no real deposit amount has been selected or funded.
- The example adds a stricter PHP 500 lifetime-spend cap and a PHP 50 per-action cap.
  These are conservative simulation defaults, not approved live purchasing limits.
- Only explicitly approved `vendor_` references and one immutable `route_` reference
  can be automated. The implemented expense execution is synthetic, not a GoTyme
  consumer card API or a verified merchant purchasing adapter.
- Declared HTTPS evidence must be fresh and not future-dated; the code binds it into
  the immutable observation. It does not prove source truth, demand or profitability.
- No subscriptions, borrowing, live markets, automatic top-ups or automatic spending
  increases from incoming earnings. No daily reset, recurring scheduler or discovery
  crawler exists in this increment.
- Payouts draw only on recorded synthetic profit after prior distributions/fees, keep
  genesis capital protected and wait for all experiments/commitments to close.
- Default PAPER/zero-capital config and all real execution flags remain false.

## Verified provider references — reviewed 2026-10-12

- [GoTyme virtual Visa payments](https://www.gotyme.com.ph/payments/): online payment
  support does not establish an autonomous purchasing API or restricted card program.
- [PayMongo hosted checkout quick start](https://docs.paymongo.com/docs/payment-channels-hosted-checkout-quick-start): test key/KYC and hosted customer payments.
- [PayMongo v2 checkout guide](https://docs.paymongo.com/docs/payment-channels-hosted-checkout): v2 is recommended for new creation flows.
- [Retrieve checkout](https://docs.paymongo.com/re/reference/get_checkout_sessions): authenticated read endpoint.
- [Transfer best practices](https://docs.paymongo.com/docs/money-movement-best-practices): stable idempotency/ref keys, approximate retention and read reconciliation.
- [Transfer simulator cases](https://docs.paymongo.com/docs/transfer-test-cases): published fake destination numbers; no real funds in test mode.
- [Create batch transfer](https://docs.paymongo.com/reference/create-batch-transfer): current v2 write route.
- [Supported bank table](https://docs.paymongo.com/docs/money-movement-list-of-banks): lists GoTyme for PESONet, not InstaPay at review time. This is provider-specific coverage, not a statement that GoTyme cannot receive InstaPay generally.
- [Bank configuration](https://docs.paymongo.com/docs/money-movement-bank-configuration): registered-business and destination requirements depend on account type/capability.
- [Webhook signature setup](https://docs.paymongo.com/docs/developer-tools-webhook-setup-management): test/live signatures and raw-body verification.

Real activation is blocked by provider onboarding/test credentials, authenticated
sandbox smoke tests, a verified one-off expense vendor/secure saved-card path,
authoritative cleared-wallet reconciliation, verified own-bank destination,
production hosting/security/monitoring, tax/refund reserves, strategy evidence and
separately reviewed live controls. A working simulation is not a profitability claim.

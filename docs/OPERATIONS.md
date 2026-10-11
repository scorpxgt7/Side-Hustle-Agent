# Operations

## Tests
Run with Python 3.11+:

```sh
python -m pip install -e .
python -m pip install pytest
python -m pytest
python -m compileall -q src tests
```

## Paper-only flow
Opportunity -> EconomicEvaluator -> RiskGovernor -> PAPER_TEST or REJECT -> SQLite decision and audit entry.
PAPER_TEST does **not** place an order, reserve money, or authorize execution.

## Security
No financial credentials in prompts, repository, configuration, tests, or logs. No live adapters are implemented. Reject malformed financial inputs. Do not override risk checks manually.

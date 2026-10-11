# Side-Hustle Agent

**Status: Phase 1 paper-only prototype; no real-money execution.**

This repository contains an opportunity scoring model, deterministic risk governor, and SQLite decision/audit persistence. It does not yet contain a trading simulator, reconciled paper ledger, real-money integrations, discovery scheduler, or evidence of profitability.

## Setup

Requires Python 3.11+:

```sh
python -m pip install -e .
python -m pip install pytest
python -m pytest
python -m compileall -q src tests
```

`PAPER_TEST` means an opportunity passed preliminary checks; **it is not an order, fill, capital reservation, or authorization to spend**.

## Important reconstruction note

The repository previously tracked initial and Phase 1 tar.gz archives without corresponding source files. The newly added code is reconstructed from documented Phase 1 interfaces, **not a verified extraction of the existing archive**. The archive is retained unchanged. Validate equivalence before relying on legacy behavior.

## Context protocol

Before any change, read `AGENTS.md`, `docs/PROJECT_CONTEXT.md`, `docs/CHANGELOG.md`, `docs/OPERATIONS.md`, and `docs/QA.md`. After any change, update the applicable context documents and verify tests.

## Security

Never commit or share card details, CVVs, passwords, private keys, seed phrases, or API secrets. AI output cannot override risk limits. Live execution remains disabled until independently reviewed safety controls and explicit approvals exist.

See `PROMPT.md` for the broader long-term plan, and `docs/PROJECT_CONTEXT.md` for verified implementation limits.

# AGENTS.md

## Mandatory context protocol

Before ANY change to this project, read:

1. `docs/PROJECT_CONTEXT.md`
2. `docs/CHANGELOG.md`
3. `docs/OPERATIONS.md`
4. `docs/QA.md`

After ANY change:

- Update `docs/CHANGELOG.md`.
- Update `docs/PROJECT_CONTEXT.md` when architecture, behavior, decisions, interfaces, limits, dependencies, blockers, or next steps change.
- Update `docs/OPERATIONS.md` when procedures change.
- Update `docs/QA.md` when acceptance criteria or verification changes.
- Never claim a change is complete without verification.

## Safety

Never introduce unrestricted financial execution. Never store payment-card data or secrets. Keep live execution disabled unless explicit, separately reviewed controls are implemented.

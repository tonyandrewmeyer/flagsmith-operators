# Contributing

Thanks for your interest in improving the Flagsmith charmed operators. This
document covers how the repository is organised and how to develop, test and
submit changes.

## Repository model

This is a monorepo of four independent [charmcraft](https://canonical.com/charmcraft)
projects under [`charms/`](charms), plus a deployable [`bundle/`](bundle) and
shared [`docs/`](docs). Each charm is self-contained: it has its own
`charmcraft.yaml`, `pyproject.toml`, `uv.lock`, `tox.ini`, sources and tests.

## Prerequisites

- [`charmcraft`](https://snapcraft.io/charmcraft) `>= 4.x` (`sudo snap install charmcraft --classic`)
- [`juju`](https://snapcraft.io/juju) `>= 3.6`
- [`astral-uv`](https://snapcraft.io/astral-uv) (`sudo snap install astral-uv --classic`)
- A Kubernetes cloud bootstrapped into Juju for integration tests
  (e.g. via [`concierge`](https://snapcraft.io/concierge) or microk8s)
- Optionally [`just`](https://github.com/casey/just) for the top-level recipes

## Development workflow

All quality gates run through `tox` inside each charm directory:

```bash
cd charms/flagsmith-api
tox -e format   # auto-format with ruff
tox -e lint     # ruff check + codespell + pyright
tox -e unit     # ops-scenario unit tests with coverage
charmcraft pack # build the .charm artifact
tox -e integration  # deploy + assert against a live Juju k8s model
```

From the repository root, the [`justfile`](justfile) fans these out:

```bash
just lint        # lint every charm
just unit        # unit-test every charm
just pack        # pack every charm
just fmt         # format every charm
```

## Design conventions

Every charm in this repository follows the same patterns. Please keep new code
consistent with them:

1. **Holistic / reconciler charms.** Charms observe lifecycle and relation
   events, but every handler delegates to a single idempotent `_reconcile()`
   method that (re)derives the complete desired state from the model and
   applies it. We do not stash event-specific data on the charm or in
   `StoredState`. This makes behaviour deterministic and easy to test.
2. **Workload logic is isolated.** Pure functions that build environments,
   render Pebble layers or talk to the workload live in a standalone module
   (`src/flagsmith_*.py`) with no charming imports, so they are unit-testable
   in isolation.
3. **Status reflects reality.** `collect-unit-status` sets `BlockedStatus`
   (operator must act, e.g. missing required relation), `WaitingStatus`
   (waiting on another component), `MaintenanceStatus` (work in progress) or
   `ActiveStatus`. Status messages are actionable.
4. **Secrets, never plaintext.** Credentials arrive over relations or Juju
   user secrets and are passed to the workload via Pebble environment, never
   written to config or logs.
5. **Migrations are leader-only and guarded.** Only the `flagsmith-api` leader
   runs database migrations, to avoid races when units scale.

## Testing standards

- Unit tests use [`ops.testing`](https://documentation.ubuntu.com/ops/latest/explanation/testing/)
  (Scenario). Test observable behaviour and branches — missing relations,
  leader vs non-leader, config edge cases — not framework plumbing.
- Integration tests use [`jubilant`](https://github.com/canonical/jubilant) and
  must clean up after themselves.
- Do not weaken or skip tests to make a change pass.

## Commit and PR conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `refactor:`, `chore:`).
- Keep commits focused; one logical change per commit.
- Open a PR against `main`; CI must pass before review.

## Code of Conduct

This project adheres to the [Ubuntu Code of Conduct](CODE_OF_CONDUCT.md).

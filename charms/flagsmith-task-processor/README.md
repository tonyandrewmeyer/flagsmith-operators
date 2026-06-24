# flagsmith-task-processor

The Charmed Operator for the **Flagsmith task processor** — the asynchronous
worker behind [Flagsmith](https://flagsmith.com). It consumes background tasks
from the shared PostgreSQL database (webhooks, audit logging, scheduled flag
changes, analytics writes), keeping that work off the API request path.

Running the task processor is **strongly recommended in production**. Without
it, the API performs this work inline and request latency suffers under load.

It is a Kubernetes sidecar charm operating the upstream `flagsmith/flagsmith`
image (started with `run-task-processor`) under Pebble.

## Requirements

- A Kubernetes cloud added to Juju (Juju `>= 3.6`).
- A deployed [`flagsmith-api`](../flagsmith-api) application.

## Usage

```bash
juju deploy flagsmith-task-processor \
    --resource flagsmith-image=flagsmith/flagsmith:2.245.0
juju integrate flagsmith-task-processor flagsmith-api
```

The charm receives its database URL and Django secret key from `flagsmith-api`
over the `flagsmith-api` relation — there is no separate database relation, so
the processor and API always agree on the database and signing key.

## Configuration

- `num-threads` — worker threads per unit (`TASK_PROCESSOR_NUM_THREADS`).
- `sleep-interval-ms` — queue poll interval; lower means lower task latency but
  more database polling.
- `queue-pop-size` — tasks fetched per poll.
- `grace-period-ms` — how long a task may overrun before being recovered.
- `extra-env` — JSON escape hatch for additional environment variables.

## Scaling

```bash
juju scale-application flagsmith-task-processor 3
```

The processor is stateless and safely horizontally scalable; the
`flagsmith-api` charm owns database migrations, so no leader coordination is
required here.

## Observability

When related to COS the charm provides a Prometheus scrape job (`/metrics`,
enabled via `PROMETHEUS_ENABLED`), forwards logs to Loki, exports OTLP traces to
Tempo, and bundles alert rules for availability and error-log spikes.

## Contributing

See the repository [CONTRIBUTING.md](../../CONTRIBUTING.md). Licensed under
Apache-2.0.

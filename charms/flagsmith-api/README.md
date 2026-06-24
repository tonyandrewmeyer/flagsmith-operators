# flagsmith-api

The Charmed Operator for the **Flagsmith API** — the Django/Gunicorn service
behind [Flagsmith](https://flagsmith.com), an open-source feature flag, remote
config and A/B testing platform. This charm runs the REST API used by SDKs and
the management dashboard, performs authentication/authorisation, and **owns the
PostgreSQL schema** (it runs and guards migrations).

It is a Kubernetes sidecar charm: it operates the upstream
`flagsmith/flagsmith` OCI image under [Pebble](https://documentation.ubuntu.com/pebble/).

## Requirements

- A Kubernetes cloud added to Juju (Juju `>= 3.6`).
- A PostgreSQL provider, e.g. [`postgresql-k8s`](https://charmhub.io/postgresql-k8s).

## Usage

```bash
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy flagsmith-api --resource flagsmith-image=flagsmith/flagsmith:2.245.0
juju integrate flagsmith-api postgresql-k8s
```

Create your first administrator:

```bash
juju run flagsmith-api/leader create-admin-user email=admin@example.com
```

### Optional integrations

| Integrate with | Effect |
| --- | --- |
| `redis-k8s` | Enables the environment-document cache and API throttling. |
| `traefik-k8s` (`ingress`) | Exposes the API on an external URL with optional TLS. |
| `flagsmith-task-processor` | Moves async work off the request path (recommended). |
| `prometheus-k8s` / `loki-k8s` / `grafana-k8s` / `tempo` (COS) | Metrics, logs, dashboard, traces and alerts. |

```bash
juju deploy redis-k8s --channel latest/edge --trust
juju integrate flagsmith-api redis-k8s

juju deploy traefik-k8s --trust
juju integrate flagsmith-api:ingress traefik-k8s
```

## Configuration

Key options (`juju config flagsmith-api <key>=<value>`):

- `secret-key` — Juju user-secret ID with the Django `SECRET_KEY`. If unset the
  charm generates and persists a key in peer data. **Set this explicitly in
  production** so it survives a full teardown.
- `gunicorn-workers`, `gunicorn-threads`, `gunicorn-timeout` — tune the WSGI
  server per unit.
- `allowed-hosts` — `DJANGO_ALLOWED_HOSTS`; restrict in production.
- `prevent-signup` — disable new dashboard signups once your admin exists.
- `enable-admin-dashboard` — serve the bundled frontend from the API; disable
  when running a dedicated `flagsmith-frontend`.
- `extra-env` — JSON escape hatch for any Flagsmith setting not exposed above
  (e.g. SMTP). Overrides charm-managed values of the same name.

See `juju config flagsmith-api` for the full list.

## Day-2 operations

| Action | Purpose |
| --- | --- |
| `create-admin-user` | Create or reset a superuser; returns a generated password if none supplied. |
| `rotate-secret-key` | Rotate the Django secret key and roll-restart the API. |
| `run-migrations` | Apply outstanding migrations during a maintenance window (also automatic on install/upgrade). |
| `dump-environment-document` | Dump an environment document JSON for debugging SDK/edge-proxy behaviour. |

### Scaling

```bash
juju scale-application flagsmith-api 3
```

The API is stateless; only the leader runs migrations, so scaling is safe.

### Upgrades

```bash
juju refresh flagsmith-api --resource flagsmith-image=flagsmith/flagsmith:<new-tag>
```

The charm re-runs migrations (leader-only) before the new workers serve traffic.

## Observability

When related to COS, the charm provides a Prometheus scrape job
(`/metrics`, enabled via `PROMETHEUS_ENABLED`), forwards container logs to Loki,
ships a Grafana dashboard, exports OTLP traces to Tempo, and bundles alert rules
for availability, 5xx rate and latency.

## Contributing

See the repository [CONTRIBUTING.md](../../CONTRIBUTING.md). Licensed under
Apache-2.0.

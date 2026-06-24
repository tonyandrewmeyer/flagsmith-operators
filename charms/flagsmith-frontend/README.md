# flagsmith-frontend

The Charmed Operator for the dedicated **Flagsmith dashboard frontend** — the
React single-page application served by a small Node server that proxies API
calls to the [Flagsmith](https://flagsmith.com) API.

Running a dedicated frontend lets you scale and route the dashboard
independently of the API. It is an alternative to the API serving the bundled
dashboard itself.

It is a Kubernetes sidecar charm operating the upstream
`flagsmith/flagsmith-frontend` image (started with `node ./api/index`, listening
on port 8080, health at `/health`) under Pebble.

## Requirements

- A Kubernetes cloud added to Juju (Juju `>= 3.6`).
- A deployed [`flagsmith-api`](../flagsmith-api) application.

## Usage

```bash
juju deploy flagsmith-frontend \
    --resource flagsmith-image=flagsmith/flagsmith-frontend:2.245.0
juju integrate flagsmith-frontend flagsmith-api

# Avoid serving two dashboards: let this charm own the frontend.
juju config flagsmith-api enable-admin-dashboard=false
```

Expose it externally via Traefik:

```bash
juju integrate flagsmith-frontend:ingress traefik-k8s
```

## Configuration

- `api-url` — explicit API base URL override (e.g. `https://flags.example.com`).
  Any trailing `/api/v1` is stripped automatically. If unset, the URL is taken
  from the `flagsmith-api` relation.
- `disable-analytics` — disable frontend analytics/telemetry.
- `extra-env` — JSON escape hatch for additional environment variables.

## Scaling

```bash
juju scale-application flagsmith-frontend 2
```

The frontend is stateless and safely horizontally scalable.

## Observability

When related to COS the charm forwards logs to Loki and provides a Grafana
dashboard endpoint.

## Contributing

See the repository [CONTRIBUTING.md](../../CONTRIBUTING.md). Licensed under
Apache-2.0.

# flagsmith-edge-proxy

The Charmed Operator for the **Flagsmith Edge Proxy** — a small read-only
service that caches environment documents from the [Flagsmith](https://flagsmith.com)
API and serves flag-evaluation requests to SDKs at very low latency, reducing
polling load on the API.

It is a Kubernetes sidecar charm operating the upstream `flagsmith/edge-proxy`
image (started with `edge-proxy-serve`, listening on port 8000, health at
`/proxy/health/liveness` and `/proxy/health/readiness`) under Pebble. The proxy
has no database.

## Requirements

- A Kubernetes cloud added to Juju (Juju `>= 3.6`).
- A reachable Flagsmith API — either the [`flagsmith-api`](../flagsmith-api)
  charm via relation, or an external API via the `api-url` config.

## Usage

```bash
juju deploy flagsmith-edge-proxy \
    --resource flagsmith-image=flagsmith/edge-proxy:2.23.0
juju integrate flagsmith-edge-proxy flagsmith-api

# Configure which environments the proxy serves.
juju config flagsmith-edge-proxy environment-key-pairs='[
  {"server_side_key": "ser.xxxx", "client_side_key": "yyyy"}
]'
```

Point your server-side SDKs at the proxy's `/api/v1/` endpoint to use it.

## Configuration

- `environment-key-pairs` — **required**. JSON array of `{server_side_key,
  client_side_key}` objects; the proxy serves nothing until at least one is set.
- `api-url` — explicit API base URL override; `/api/v1` is appended
  automatically. Required if no `flagsmith-api` relation is present.
- `api-poll-frequency-seconds` / `api-poll-timeout-seconds` — control how often
  and how patiently the proxy polls the API for environment changes.
- `allow-origins` — CORS `Access-Control-Allow-Origin` value.
- `web-concurrency` — Uvicorn workers per unit (keep at 1 and scale out).
- `extra-env` — JSON escape hatch for additional environment variables.

## Scaling

```bash
juju scale-application flagsmith-edge-proxy 3
```

The proxy is stateless. Per upstream guidance, keep `web-concurrency=1` and run
multiple units behind a load balancer (e.g. via Traefik ingress).

## Observability

The Edge Proxy does not expose a Prometheus metrics endpoint, so this charm
relies on log forwarding to Loki, with an alert rule for error-log spikes
(e.g. when the proxy cannot reach the API).

## Contributing

See the repository [CONTRIBUTING.md](../../CONTRIBUTING.md). Licensed under
Apache-2.0.

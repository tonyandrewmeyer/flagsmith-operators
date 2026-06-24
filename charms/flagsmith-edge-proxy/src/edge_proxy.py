# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Workload logic for the Flagsmith Edge Proxy.

Pure, ops-free helpers for the Flagsmith Edge Proxy — a small read-only service
that caches environment documents fetched from the Flagsmith API and serves
flag-evaluation requests to SDKs at low latency. It has no database.

Upstream facts (verified against github.com/Flagsmith/edge-proxy and the
official Edge Proxy documentation, June 2026):
- Image: ``flagsmith/edge-proxy`` (Python/uvicorn).
- Start command: ``edge-proxy-serve`` (the image's default entrypoint).
- Listen port: ``8000``.
- Health endpoints: ``/proxy/health/liveness`` and ``/proxy/health/readiness``.
- Configuration via env vars or ``/app/config.json``; env takes precedence.
  Relevant env: ``API_URL`` (the self-hosted API, including ``/api/v1``),
  ``ENVIRONMENT_KEY_PAIRS`` (JSON array of {server_side_key, client_side_key}),
  ``API_POLL_FREQUENCY_SECONDS``, ``API_POLL_TIMEOUT_SECONDS``,
  ``ALLOW_ORIGINS``, ``WEB_CONCURRENCY``, ``LOGGING``.
- The Edge Proxy does NOT expose a Prometheus ``/metrics`` endpoint, so this
  charm relies on logs for observability.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CONTAINER_NAME = "flagsmith-edge-proxy"
SERVICE_NAME = "flagsmith-edge-proxy"
START_COMMAND = "edge-proxy-serve"
PORT = 8000
LIVENESS_PATH = "/proxy/health/liveness"
READINESS_PATH = "/proxy/health/readiness"


@dataclass
class EdgeProxyConfig:
    """Desired configuration for the Flagsmith Edge Proxy workload."""

    api_url: str | None = None
    environment_key_pairs: str = "[]"
    api_poll_frequency_seconds: int = 10
    api_poll_timeout_seconds: int = 5
    # The upstream proxy uses pydantic-settings to parse ALLOW_ORIGINS as a
    # JSON list; passing a bare string (e.g. "*") raises a SettingsError.
    allow_origins: str = '["*"]'
    web_concurrency: int = 1
    log_level: str = "INFO"
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Whether the proxy has an API URL and at least one environment pair."""
        if not self.api_url:
            return False
        try:
            return len(validate_environment_key_pairs(self.environment_key_pairs)) > 0
        except ValueError:
            return False


def api_evaluation_url(raw: str | None) -> str | None:
    """Normalise an API URL to include the ``/api/v1`` path the proxy polls."""
    if not raw:
        return None
    url = raw.rstrip("/")
    if url.endswith("/api/v1"):
        return url
    if url.endswith("/api"):
        return url + "/v1"
    return url + "/api/v1"


def validate_environment_key_pairs(raw: str) -> list[dict[str, str]]:
    """Validate and parse the ``environment-key-pairs`` config.

    Must be a JSON array of objects, each with ``server_side_key`` and
    ``client_side_key``. Returns the parsed list (possibly empty for ``[]``).
    Raises ValueError on malformed input.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"environment-key-pairs is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("environment-key-pairs must be a JSON array")
    for entry in data:
        if (
            not isinstance(entry, dict)
            or not {
                "server_side_key",
                "client_side_key",
            }
            <= entry.keys()
        ):
            raise ValueError(
                "each environment-key-pairs entry needs server_side_key and client_side_key"
            )
    return data


def build_environment(config: EdgeProxyConfig) -> dict[str, str]:
    """Build the workload environment from desired configuration."""
    env: dict[str, str] = {
        "ENVIRONMENT_KEY_PAIRS": config.environment_key_pairs,
        "API_POLL_FREQUENCY_SECONDS": str(config.api_poll_frequency_seconds),
        "API_POLL_TIMEOUT_SECONDS": str(config.api_poll_timeout_seconds),
        "ALLOW_ORIGINS": config.allow_origins,
        "WEB_CONCURRENCY": str(config.web_concurrency),
        "LOGGING": json.dumps({"log_level": config.log_level, "log_format": "json"}),
    }
    if config.api_url:
        env["API_URL"] = config.api_url
    env.update({k: str(v) for k, v in config.extra_env.items()})
    return env


def build_layer(config: EdgeProxyConfig) -> dict[str, Any]:
    """Build the Pebble layer that runs and health-checks the edge proxy."""
    return {
        "summary": "Flagsmith edge proxy layer",
        "description": "Runs the Flagsmith Edge Proxy (uvicorn).",
        "services": {
            SERVICE_NAME: {
                "override": "replace",
                "summary": "Flagsmith edge proxy",
                "command": START_COMMAND,
                "working-dir": "/app",
                "startup": "enabled",
                "environment": build_environment(config),
            }
        },
        "checks": {
            "edge-ready": {
                "override": "replace",
                "level": "ready",
                "period": "30s",
                "timeout": "5s",
                "threshold": 3,
                "http": {"url": f"http://localhost:{PORT}{READINESS_PATH}"},
            },
            "edge-alive": {
                "override": "replace",
                "level": "alive",
                "period": "30s",
                "timeout": "5s",
                "threshold": 5,
                "http": {"url": f"http://localhost:{PORT}{LIVENESS_PATH}"},
            },
        },
    }


def parse_extra_env(raw: str) -> dict[str, str]:
    """Parse the ``extra-env`` config (JSON mapping). Empty input -> {}."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"extra-env is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("extra-env must be a mapping of name to value")
    return {str(k): str(v) for k, v in data.items()}

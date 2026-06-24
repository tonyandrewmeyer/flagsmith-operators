# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Workload logic for the Flagsmith dashboard frontend.

Pure, ops-free helpers for the dedicated Flagsmith dashboard frontend — the
React SPA served by a small Node server (the ``flagsmith/flagsmith-frontend``
image). The server proxies API calls to the Flagsmith API, whose base URL is
provided via the ``PROXY_API_URL`` / ``FLAGSMITH_PROXY_API_URL`` environment
variables.

Upstream facts (verified against frontend/package.json and the official Helm
chart, June 2026):
- Start command: ``node ./api/index`` with ``NODE_ENV=production`` (package.json
  ``start`` script; this is the image's default CMD).
- Listen port: ``8080`` (chart ``service.frontend.port``).
- Health endpoint: ``GET /health`` (chart liveness/readiness probes).
- API base env: ``PROXY_API_URL`` and ``FLAGSMITH_PROXY_API_URL`` point at the
  API root (host:port), NOT the ``/api/v1/`` path.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CONTAINER_NAME = "flagsmith-frontend"
SERVICE_NAME = "flagsmith-frontend"
PORT = 8080
HEALTH_PATH = "/health"
# Workdir inside the official flagsmith/flagsmith-frontend image; the Node
# server's entrypoint (api/index) is resolved relative to it.
WORKING_DIR = "/srv/bt"
START_COMMAND = "node ./api/index"


@dataclass
class FrontendConfig:
    """Desired configuration for the Flagsmith frontend workload."""

    api_url: str | None = None
    log_level: str = "INFO"
    disable_analytics: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Whether the API base URL is known."""
        return bool(self.api_url)


def api_base_url(raw: str | None) -> str | None:
    """Normalise an API URL to its root (scheme://host:port), no trailing slash.

    The frontend server proxies to the API root and appends paths itself, so we
    must strip any ``/api/v1`` suffix that a relation or operator may include.
    """
    if not raw:
        return None
    url = raw.rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


def build_environment(config: FrontendConfig) -> dict[str, str]:
    """Build the workload environment from desired configuration."""
    env: dict[str, str] = {
        "NODE_ENV": "production",
        "PORT": str(PORT),
        "LOG_LEVEL": config.log_level,
    }
    if config.api_url:
        env["PROXY_API_URL"] = config.api_url
        env["FLAGSMITH_PROXY_API_URL"] = config.api_url
    if config.disable_analytics:
        env["DISABLE_ANALYTICS"] = "true"
    env.update({k: str(v) for k, v in config.extra_env.items()})
    return env


def build_layer(config: FrontendConfig) -> dict[str, Any]:
    """Build the Pebble layer that runs and health-checks the frontend."""
    return {
        "summary": "Flagsmith frontend layer",
        "description": "Runs the Flagsmith dashboard frontend Node server.",
        "services": {
            SERVICE_NAME: {
                "override": "replace",
                "summary": "Flagsmith dashboard frontend",
                "command": START_COMMAND,
                "working-dir": WORKING_DIR,
                "startup": "enabled",
                "environment": build_environment(config),
            }
        },
        "checks": {
            "frontend-ready": {
                "override": "replace",
                "level": "ready",
                "period": "30s",
                "timeout": "5s",
                "threshold": 3,
                "http": {"url": f"http://localhost:{PORT}{HEALTH_PATH}"},
            },
            "frontend-alive": {
                "override": "replace",
                "level": "alive",
                "period": "30s",
                "timeout": "5s",
                "threshold": 5,
                "http": {"url": f"http://localhost:{PORT}{HEALTH_PATH}"},
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

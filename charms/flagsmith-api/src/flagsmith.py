# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Workload logic for the Flagsmith API.

This module is deliberately free of any charming concerns (no ``ops`` imports).
It contains pure helpers that build the workload environment and Pebble layer,
and thin wrappers for talking to the running workload. Keeping this logic here
makes it straightforward to unit-test in isolation from the charm.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Pebble service and container names.
SERVICE_NAME = "flagsmith-api"
CONTAINER_NAME = "flagsmith-api"

# The Flagsmith image entrypoint (scripts/run-docker.sh) dispatches on argv[0].
ENTRYPOINT = "/app/scripts/run-docker.sh"

# The port the API listens on inside the container.
API_PORT = 8000

# Health and introspection endpoints exposed by the Django app.
LIVENESS_PATH = "/health/liveness/"
READINESS_PATH = "/health/readiness/"
VERSION_PATH = "/version/"
METRICS_PATH = "/metrics/"


@dataclass
class FlagsmithConfig:
    """The complete desired configuration for a Flagsmith API workload.

    Built by the charm from config and relation data, then turned into the
    workload environment. Optional datastore fields are ``None`` until the
    corresponding relation provides them.
    """

    database_url: str | None = None
    redis_url: str | None = None
    secret_key: str | None = None
    domain: str | None = None
    allowed_hosts: str = "*"
    log_level: str = "INFO"
    gunicorn_workers: int = 3
    gunicorn_threads: int = 2
    gunicorn_timeout: int = 30
    serve_frontend: bool = True
    prevent_signup: bool = False
    enable_telemetry: bool = True
    use_postgres_for_analytics: bool = True
    prometheus_enabled: bool = True
    task_processor_enabled: bool = False
    tracing_endpoint: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Whether the minimum required configuration to start is present."""
        return bool(self.database_url and self.secret_key)


def build_environment(config: FlagsmithConfig) -> dict[str, str]:
    """Build the workload environment variables from desired configuration.

    Only includes variables that are meaningfully set, so that Flagsmith's own
    defaults apply otherwise. Operator-supplied ``extra_env`` is layered last
    and therefore wins, acting as an escape hatch.
    """
    env: dict[str, str] = {
        "DJANGO_ALLOWED_HOSTS": config.allowed_hosts,
        "LOG_LEVEL": config.log_level,
        "LOG_FORMAT": "json",
        "ENVIRONMENT": "production",
        "GUNICORN_WORKERS": str(config.gunicorn_workers),
        "GUNICORN_THREADS": str(config.gunicorn_threads),
        "GUNICORN_TIMEOUT": str(config.gunicorn_timeout),
        # The API owns migrations via the charm; don't let the entrypoint's
        # serve path race on them across scaled units.
        "SKIP_WAIT_FOR_DB": "",
        "PREVENT_SIGNUP": _bool(config.prevent_signup),
        "ENABLE_TELEMETRY": _bool(config.enable_telemetry),
        "USE_POSTGRES_FOR_ANALYTICS": _bool(config.use_postgres_for_analytics),
        "SERVE_FE_ASSETS": _bool(config.serve_frontend),
        "PROMETHEUS_ENABLED": _bool(config.prometheus_enabled),
    }
    if config.database_url:
        env["DATABASE_URL"] = config.database_url
    if config.redis_url:
        # Flagsmith uses a Redis cache for environment documents and throttling.
        env["CACHE_FLAGS_SECONDS"] = "60"
        env["CACHE_BAD_ENVIRONMENTS_SECONDS"] = "60"
        env["CACHE_LOCATION"] = config.redis_url
        env["CACHE_BACKEND"] = "django_redis.cache.RedisCache"
    if config.secret_key:
        env["DJANGO_SECRET_KEY"] = config.secret_key
    if config.domain:
        env["FLAGSMITH_DOMAIN"] = config.domain
    if config.task_processor_enabled:
        env["TASK_RUN_METHOD"] = "TASK_PROCESSOR"
    if config.tracing_endpoint:
        # Flagsmith supports OpenTelemetry OTLP export natively.
        env["OPENTELEMETRY_ENABLED"] = "true"
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = config.tracing_endpoint
        env["OTEL_SERVICE_NAME"] = "flagsmith-api"
    # Operator escape hatch wins.
    env.update({k: str(v) for k, v in config.extra_env.items()})
    return env


def build_layer(config: FlagsmithConfig) -> dict[str, Any]:
    """Build the Pebble layer dict that runs and health-checks the API."""
    return {
        "summary": "Flagsmith API layer",
        "description": "Runs the Flagsmith Django API under Gunicorn.",
        "services": {
            SERVICE_NAME: {
                "override": "replace",
                "summary": "Flagsmith API (gunicorn)",
                "command": f"{ENTRYPOINT} serve",
                "startup": "enabled",
                "environment": build_environment(config),
            }
        },
        "checks": {
            "api-ready": {
                "override": "replace",
                "level": "ready",
                "period": "30s",
                "timeout": "5s",
                "threshold": 3,
                "http": {"url": f"http://localhost:{API_PORT}{READINESS_PATH}"},
            },
            "api-alive": {
                "override": "replace",
                "level": "alive",
                "period": "30s",
                "timeout": "5s",
                "threshold": 5,
                "http": {"url": f"http://localhost:{API_PORT}{LIVENESS_PATH}"},
            },
        },
    }


def parse_extra_env(raw: str) -> dict[str, str]:
    """Parse the ``extra-env`` config (JSON or trivial YAML mapping).

    Returns an empty dict for empty/blank input. Raises ValueError if the input
    is not a mapping, so the charm can surface a BlockedStatus.
    """
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


def postgresql_uri(*, host: str, port: str, username: str, password: str, database: str) -> str:
    """Assemble a SQLAlchemy/Django-style PostgreSQL connection URL."""
    return f"postgresql://{username}:{password}@{host}:{port}/{database}"


def _bool(value: bool) -> str:
    return "true" if value else "false"

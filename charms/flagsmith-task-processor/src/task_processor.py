# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Workload logic for the Flagsmith task processor.

Pure, ops-free helpers for the Flagsmith async task processor. The processor
runs the same ``flagsmith/flagsmith`` image as the API, started with the
``run-task-processor`` subcommand. It consumes tasks (webhooks, audit logging,
scheduled flag changes, analytics) from the shared PostgreSQL database, so it
needs the same ``DATABASE_URL`` and ``DJANGO_SECRET_KEY`` as the API — both of
which it receives over the ``flagsmith-api`` relation.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CONTAINER_NAME = "flagsmith-task-processor"
SERVICE_NAME = "flagsmith-task-processor"
ENTRYPOINT = "/app/scripts/run-docker.sh"
PORT = 8000
LIVENESS_PATH = "/health/liveness/"
READINESS_PATH = "/health/readiness/"
METRICS_PATH = "/metrics/"


@dataclass
class TaskProcessorConfig:
    """Desired configuration for the Flagsmith task processor workload."""

    database_url: str | None = None
    secret_key: str | None = None
    log_level: str = "INFO"
    # The processor's image dispatches via gunicorn and exposes Django's
    # /health/ endpoints, so Django's ALLOWED_HOSTS must permit the host used
    # by Pebble's HTTP checks ("localhost").
    allowed_hosts: str = "*"
    num_threads: int = 5
    sleep_interval_ms: int = 500
    queue_pop_size: int = 10
    grace_period_ms: int = 20000
    prometheus_enabled: bool = True
    tracing_endpoint: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Whether the minimum required configuration to start is present."""
        return bool(self.database_url and self.secret_key)


def build_environment(config: TaskProcessorConfig) -> dict[str, str]:
    """Build the workload environment from desired configuration."""
    env: dict[str, str] = {
        "LOG_LEVEL": config.log_level,
        "LOG_FORMAT": "json",
        "ENVIRONMENT": "production",
        "DJANGO_ALLOWED_HOSTS": config.allowed_hosts,
        "PROMETHEUS_ENABLED": _bool(config.prometheus_enabled),
        "TASK_PROCESSOR_SLEEP_INTERVAL_MS": str(config.sleep_interval_ms),
        "TASK_PROCESSOR_NUM_THREADS": str(config.num_threads),
        "TASK_PROCESSOR_QUEUE_POP_SIZE": str(config.queue_pop_size),
        "TASK_PROCESSOR_GRACE_PERIOD_MS": str(config.grace_period_ms),
    }
    if config.database_url:
        env["DATABASE_URL"] = config.database_url
    if config.secret_key:
        env["DJANGO_SECRET_KEY"] = config.secret_key
    if config.tracing_endpoint:
        env["OPENTELEMETRY_ENABLED"] = "true"
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = config.tracing_endpoint
        env["OTEL_SERVICE_NAME"] = "flagsmith-task-processor"
    env.update({k: str(v) for k, v in config.extra_env.items()})
    return env


def build_layer(config: TaskProcessorConfig) -> dict[str, Any]:
    """Build the Pebble layer that runs and health-checks the task processor."""
    return {
        "summary": "Flagsmith task processor layer",
        "description": "Runs the Flagsmith async task processor.",
        "services": {
            SERVICE_NAME: {
                "override": "replace",
                "summary": "Flagsmith task processor",
                "command": f"{ENTRYPOINT} run-task-processor",
                "working-dir": "/app",
                "startup": "enabled",
                "environment": build_environment(config),
            }
        },
        "checks": {
            "tp-ready": {
                "override": "replace",
                "level": "ready",
                "period": "30s",
                "timeout": "5s",
                "threshold": 3,
                "http": {"url": f"http://localhost:{PORT}{READINESS_PATH}"},
            },
            "tp-alive": {
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


def _bool(value: bool) -> str:
    return "true" if value else "false"

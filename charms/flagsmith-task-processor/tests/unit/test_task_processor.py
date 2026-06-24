# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Unit tests for the task-processor workload helper module."""

import pytest

from task_processor import (
    TaskProcessorConfig,
    build_environment,
    build_layer,
    parse_extra_env,
)


def test_config_readiness():
    assert not TaskProcessorConfig().is_ready
    assert not TaskProcessorConfig(database_url="x").is_ready
    assert not TaskProcessorConfig(secret_key="y").is_ready
    assert TaskProcessorConfig(database_url="x", secret_key="y").is_ready


def test_build_environment_minimal():
    env = build_environment(TaskProcessorConfig(database_url="postgresql://x", secret_key="k"))
    assert env["DATABASE_URL"] == "postgresql://x"
    assert env["DJANGO_SECRET_KEY"] == "k"
    assert env["TASK_PROCESSOR_NUM_THREADS"] == "5"
    assert env["TASK_PROCESSOR_SLEEP_INTERVAL_MS"] == "500"
    assert env["PROMETHEUS_ENABLED"] == "true"
    # Pebble probes the workload's Django health endpoints over "localhost",
    # so ALLOWED_HOSTS must permit it (Django returns 400 otherwise).
    assert env["DJANGO_ALLOWED_HOSTS"] == "*"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env


def test_build_environment_with_tracing_and_overrides():
    env = build_environment(
        TaskProcessorConfig(
            database_url="postgresql://x",
            secret_key="k",
            num_threads=8,
            tracing_endpoint="http://tempo:4318",
            extra_env={"TASK_PROCESSOR_NUM_THREADS": "16"},
        )
    )
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://tempo:4318"
    # extra-env wins.
    assert env["TASK_PROCESSOR_NUM_THREADS"] == "16"


def test_build_layer():
    layer = build_layer(TaskProcessorConfig(database_url="postgresql://x", secret_key="k"))
    svc = layer["services"]["flagsmith-task-processor"]
    assert svc["command"].endswith("run-task-processor")
    assert svc["startup"] == "enabled"
    assert "tp-ready" in layer["checks"]
    assert "tp-alive" in layer["checks"]


def test_parse_extra_env():
    assert parse_extra_env("") == {}
    assert parse_extra_env('{"A": 1}') == {"A": "1"}
    with pytest.raises(ValueError):
        parse_extra_env("nope")
    with pytest.raises(ValueError):
        parse_extra_env("[1,2]")

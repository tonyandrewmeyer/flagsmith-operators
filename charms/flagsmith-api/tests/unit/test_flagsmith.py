# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Unit tests for the workload helper module (no charming concerns)."""

import pytest

from flagsmith import (
    FlagsmithConfig,
    build_environment,
    build_layer,
    parse_extra_env,
    postgresql_uri,
)


def test_postgresql_uri():
    uri = postgresql_uri(
        host="db.example", port="5432", username="fs", password="pw", database="flagsmith"
    )
    assert uri == "postgresql://fs:pw@db.example:5432/flagsmith"


def test_config_not_ready_without_db_or_secret():
    assert not FlagsmithConfig().is_ready
    assert not FlagsmithConfig(database_url="x").is_ready
    assert not FlagsmithConfig(secret_key="x").is_ready
    assert FlagsmithConfig(database_url="x", secret_key="y").is_ready


def test_build_environment_minimal():
    env = build_environment(FlagsmithConfig(database_url="postgresql://x", secret_key="k"))
    assert env["DATABASE_URL"] == "postgresql://x"
    assert env["DJANGO_SECRET_KEY"] == "k"
    assert env["PROMETHEUS_ENABLED"] == "true"
    assert env["SERVE_FE_ASSETS"] == "true"
    # No redis, no tracing, no task processor unless asked.
    assert "CACHE_LOCATION" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert "TASK_RUN_METHOD" not in env


def test_build_environment_with_optional_integrations():
    env = build_environment(
        FlagsmithConfig(
            database_url="postgresql://x",
            secret_key="k",
            redis_url="redis://cache:6379",
            tracing_endpoint="http://tempo:4318",
            task_processor_enabled=True,
        )
    )
    assert env["CACHE_LOCATION"] == "redis://cache:6379"
    assert env["CACHE_BACKEND"] == "django_redis.cache.RedisCache"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://tempo:4318"
    assert env["OPENTELEMETRY_ENABLED"] == "true"
    assert env["TASK_RUN_METHOD"] == "TASK_PROCESSOR"


def test_extra_env_overrides_managed_values():
    env = build_environment(
        FlagsmithConfig(
            database_url="postgresql://x",
            secret_key="k",
            extra_env={"DJANGO_ALLOWED_HOSTS": "flags.example.com", "EMAIL_HOST": "smtp"},
        )
    )
    assert env["DJANGO_ALLOWED_HOSTS"] == "flags.example.com"
    assert env["EMAIL_HOST"] == "smtp"


def test_parse_extra_env():
    assert parse_extra_env("") == {}
    assert parse_extra_env("   ") == {}
    assert parse_extra_env('{"A": "b", "N": 1}') == {"A": "b", "N": "1"}


def test_parse_extra_env_invalid():
    with pytest.raises(ValueError):
        parse_extra_env("not json")
    with pytest.raises(ValueError):
        parse_extra_env('["a", "b"]')


def test_build_layer_has_health_checks():
    layer = build_layer(FlagsmithConfig(database_url="postgresql://x", secret_key="k"))
    assert "api-ready" in layer["checks"]
    assert "api-alive" in layer["checks"]
    assert layer["checks"]["api-ready"]["level"] == "ready"
    svc = layer["services"]["flagsmith-api"]
    assert svc["command"].endswith("serve")
    assert svc["startup"] == "enabled"

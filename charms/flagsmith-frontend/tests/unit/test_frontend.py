# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Unit tests for the frontend workload helper module."""

import pytest

from frontend import (
    FrontendConfig,
    api_base_url,
    build_environment,
    build_layer,
    parse_extra_env,
)


def test_api_base_url_strips_api_suffix():
    assert api_base_url("http://api:8000") == "http://api:8000"
    assert api_base_url("http://api:8000/") == "http://api:8000"
    assert api_base_url("http://api:8000/api/v1/") == "http://api:8000"
    assert api_base_url("https://flags.example.com/api") == "https://flags.example.com"
    assert api_base_url(None) is None


def test_config_readiness():
    assert not FrontendConfig().is_ready
    assert FrontendConfig(api_url="http://api:8000").is_ready


def test_build_environment():
    env = build_environment(FrontendConfig(api_url="http://api:8000"))
    assert env["PROXY_API_URL"] == "http://api:8000"
    assert env["FLAGSMITH_PROXY_API_URL"] == "http://api:8000"
    assert env["NODE_ENV"] == "production"
    assert env["PORT"] == "8080"
    assert "DISABLE_ANALYTICS" not in env


def test_build_environment_disable_analytics_and_override():
    env = build_environment(
        FrontendConfig(
            api_url="http://api:8000",
            disable_analytics=True,
            extra_env={"PROXY_API_URL": "http://override:9000"},
        )
    )
    assert env["DISABLE_ANALYTICS"] == "true"
    assert env["PROXY_API_URL"] == "http://override:9000"


def test_build_layer():
    layer = build_layer(FrontendConfig(api_url="http://api:8000"))
    svc = layer["services"]["flagsmith-frontend"]
    assert svc["command"] == "node ./api/index"
    assert svc["startup"] == "enabled"
    assert "frontend-ready" in layer["checks"]
    assert layer["checks"]["frontend-ready"]["http"]["url"].endswith(":8080/health")


def test_parse_extra_env():
    assert parse_extra_env("") == {}
    assert parse_extra_env('{"A": 1}') == {"A": "1"}
    with pytest.raises(ValueError):
        parse_extra_env("nope")

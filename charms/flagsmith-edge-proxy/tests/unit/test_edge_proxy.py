# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Unit tests for the edge-proxy workload helper module."""

import pytest

from edge_proxy import (
    EdgeProxyConfig,
    api_evaluation_url,
    build_environment,
    build_layer,
    parse_extra_env,
    validate_environment_key_pairs,
)

PAIRS = '[{"server_side_key": "ser.abc", "client_side_key": "xyz"}]'


def test_api_evaluation_url_appends_path():
    assert api_evaluation_url("http://api:8000") == "http://api:8000/api/v1"
    assert api_evaluation_url("http://api:8000/") == "http://api:8000/api/v1"
    assert api_evaluation_url("http://api:8000/api") == "http://api:8000/api/v1"
    assert api_evaluation_url("http://api:8000/api/v1") == "http://api:8000/api/v1"
    assert api_evaluation_url(None) is None


def test_validate_environment_key_pairs():
    assert validate_environment_key_pairs("[]") == []
    assert validate_environment_key_pairs("") == []
    parsed = validate_environment_key_pairs(PAIRS)
    assert parsed[0]["server_side_key"] == "ser.abc"


def test_validate_environment_key_pairs_invalid():
    with pytest.raises(ValueError):
        validate_environment_key_pairs("not json")
    with pytest.raises(ValueError):
        validate_environment_key_pairs('{"a": 1}')  # not a list
    with pytest.raises(ValueError):
        validate_environment_key_pairs('[{"server_side_key": "x"}]')  # missing client key


def test_config_readiness():
    assert not EdgeProxyConfig().is_ready
    assert not EdgeProxyConfig(api_url="http://api:8000/api/v1").is_ready  # no pairs
    assert not EdgeProxyConfig(environment_key_pairs=PAIRS).is_ready  # no api url
    assert EdgeProxyConfig(api_url="http://api:8000/api/v1", environment_key_pairs=PAIRS).is_ready


def test_build_environment():
    env = build_environment(
        EdgeProxyConfig(api_url="http://api:8000/api/v1", environment_key_pairs=PAIRS)
    )
    assert env["API_URL"] == "http://api:8000/api/v1"
    assert env["ENVIRONMENT_KEY_PAIRS"] == PAIRS
    assert env["API_POLL_FREQUENCY_SECONDS"] == "10"
    assert env["WEB_CONCURRENCY"] == "1"
    assert '"log_format": "json"' in env["LOGGING"]


def test_build_environment_extra_env_wins():
    env = build_environment(
        EdgeProxyConfig(
            api_url="http://api:8000/api/v1",
            environment_key_pairs=PAIRS,
            extra_env={"WEB_CONCURRENCY": "4"},
        )
    )
    assert env["WEB_CONCURRENCY"] == "4"


def test_build_layer():
    layer = build_layer(
        EdgeProxyConfig(api_url="http://api:8000/api/v1", environment_key_pairs=PAIRS)
    )
    svc = layer["services"]["flagsmith-edge-proxy"]
    assert svc["command"] == "edge-proxy-serve"
    assert "edge-ready" in layer["checks"]
    assert layer["checks"]["edge-ready"]["http"]["url"].endswith("/proxy/health/readiness")


def test_parse_extra_env():
    assert parse_extra_env("") == {}
    assert parse_extra_env('{"A": 1}') == {"A": "1"}
    with pytest.raises(ValueError):
        parse_extra_env("nope")

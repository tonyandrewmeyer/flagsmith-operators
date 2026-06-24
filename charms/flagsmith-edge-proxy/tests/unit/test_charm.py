# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Flagsmith edge-proxy charm."""

import ops
import pytest
from ops import testing

from charm import FlagsmithEdgeProxyCharm

CONTAINER = "flagsmith-edge-proxy"
SERVICE = "flagsmith-edge-proxy"
PAIRS = '[{"server_side_key": "ser.abc", "client_side_key": "xyz"}]'


@pytest.fixture
def ctx():
    return testing.Context(FlagsmithEdgeProxyCharm)


def _api_relation(api_url: str = "http://flagsmith-api.test.svc.cluster.local:8000"):
    data = {"api-url": api_url} if api_url else {}
    return testing.Relation(
        endpoint="flagsmith-api",
        interface="flagsmith_api",
        remote_app_name="flagsmith-api",
        remote_app_data=data,
    )


def test_blocked_without_api_url(ctx):
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": PAIRS},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("edge-proxy-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "API URL" in state_out.unit_status.message


def test_blocked_without_key_pairs(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("edge-proxy-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert state_out.unit_status == ops.BlockedStatus("configure environment-key-pairs")


def test_blocked_on_invalid_key_pairs(ctx):
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": "not json"},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("edge-proxy-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "environment-key-pairs" in state_out.unit_status.message


def test_active_and_service_started(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": PAIRS},
        containers={container},
        relations={testing.PeerRelation("edge-proxy-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    svc = state_out.get_container(CONTAINER).plan.services[SERVICE]
    assert svc.command == "edge-proxy-serve"
    assert svc.environment["API_URL"] == "http://flagsmith-api.test.svc.cluster.local:8000/api/v1"
    assert svc.environment["ENVIRONMENT_KEY_PAIRS"] == PAIRS
    assert state_out.unit_status == ops.ActiveStatus()


def test_api_url_config_override(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": PAIRS, "api-url": "https://flags.example.com"},
        containers={container},
        relations={testing.PeerRelation("edge-proxy-peers")},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    env = state_out.get_container(CONTAINER).plan.services[SERVICE].environment
    assert env["API_URL"] == "https://flags.example.com/api/v1"
    assert state_out.unit_status == ops.ActiveStatus()


def test_container_not_ready_is_maintenance(ctx):
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": PAIRS},
        containers={testing.Container(CONTAINER, can_connect=False)},
        relations={testing.PeerRelation("edge-proxy-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.MaintenanceStatus)


def test_invalid_extra_env_blocks(ctx):
    state_in = testing.State(
        leader=True,
        config={"environment-key-pairs": PAIRS, "extra-env": "not json"},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("edge-proxy-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "extra-env" in state_out.unit_status.message

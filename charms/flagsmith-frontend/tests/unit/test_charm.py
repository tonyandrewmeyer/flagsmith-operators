# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Flagsmith frontend charm."""

import ops
import pytest
from ops import testing

from charm import FlagsmithFrontendCharm

CONTAINER = "flagsmith-frontend"
SERVICE = "flagsmith-frontend"


@pytest.fixture
def ctx():
    return testing.Context(FlagsmithFrontendCharm)


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
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("frontend-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "API URL" in state_out.unit_status.message


def test_active_with_api_relation(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("frontend-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    svc = state_out.get_container(CONTAINER).plan.services[SERVICE]
    assert svc.command == "node ./api/index"
    assert svc.environment["PROXY_API_URL"] == "http://flagsmith-api.test.svc.cluster.local:8000"
    assert state_out.unit_status == ops.ActiveStatus()


def test_api_url_derived_strips_suffix(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={
            testing.PeerRelation("frontend-peers"),
            _api_relation("http://api:8000/api/v1/"),
        },
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    env = state_out.get_container(CONTAINER).plan.services[SERVICE].environment
    assert env["PROXY_API_URL"] == "http://api:8000"


def test_api_url_config_override_wins(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        config={"api-url": "https://flags.example.com"},
        containers={container},
        relations={testing.PeerRelation("frontend-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    env = state_out.get_container(CONTAINER).plan.services[SERVICE].environment
    assert env["PROXY_API_URL"] == "https://flags.example.com"


def test_container_not_ready_is_maintenance(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=False)},
        relations={testing.PeerRelation("frontend-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.MaintenanceStatus)


def test_invalid_extra_env_blocks(ctx):
    state_in = testing.State(
        leader=True,
        config={"extra-env": "not json", "api-url": "http://api:8000"},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("frontend-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "extra-env" in state_out.unit_status.message

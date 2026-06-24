# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Flagsmith task-processor charm."""

import ops
import pytest
from ops import testing

from charm import FlagsmithTaskProcessorCharm

CONTAINER = "flagsmith-task-processor"
SERVICE = "flagsmith-task-processor"


@pytest.fixture
def ctx():
    return testing.Context(FlagsmithTaskProcessorCharm)


def _api_relation(complete: bool = True) -> testing.Relation:
    data = {}
    if complete:
        data = {
            "api-url": "http://flagsmith-api.test.svc.cluster.local:8000",
            "secret-key": "shared-secret",
            "database-url": "postgresql://relation-1:pw@db:5432/flagsmith",
        }
    return testing.Relation(
        endpoint="flagsmith-api",
        interface="flagsmith_api",
        remote_app_name="flagsmith-api",
        remote_app_data=data,
    )


def test_blocked_without_api_relation(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("task-processor-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert state_out.unit_status == ops.BlockedStatus("missing required relation: flagsmith-api")


def test_waiting_when_databag_empty(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("task-processor-peers"), _api_relation(complete=False)},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.WaitingStatus)


def test_active_and_service_started(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("task-processor-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    svc = state_out.get_container(CONTAINER).plan.services[SERVICE]
    assert svc.command.endswith("run-task-processor")
    assert svc.environment["DATABASE_URL"] == "postgresql://relation-1:pw@db:5432/flagsmith"
    assert svc.environment["DJANGO_SECRET_KEY"] == "shared-secret"
    assert svc.environment["TASK_PROCESSOR_NUM_THREADS"] == "5"
    assert state_out.unit_status == ops.ActiveStatus()


def test_config_propagates_to_environment(ctx):
    container = testing.Container(CONTAINER, can_connect=True)
    state_in = testing.State(
        leader=True,
        config={"num-threads": 12, "sleep-interval-ms": 250},
        containers={container},
        relations={testing.PeerRelation("task-processor-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    env = state_out.get_container(CONTAINER).plan.services[SERVICE].environment
    assert env["TASK_PROCESSOR_NUM_THREADS"] == "12"
    assert env["TASK_PROCESSOR_SLEEP_INTERVAL_MS"] == "250"


def test_container_not_ready_is_maintenance(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=False)},
        relations={testing.PeerRelation("task-processor-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.MaintenanceStatus)


def test_invalid_extra_env_blocks(ctx):
    state_in = testing.State(
        leader=True,
        config={"extra-env": "not json"},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("task-processor-peers"), _api_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "extra-env" in state_out.unit_status.message


def test_service_stopped_when_databag_lost(ctx):
    # Service running, then relation data goes away -> service stopped, waiting.
    container = testing.Container(
        CONTAINER,
        can_connect=True,
        layers={
            "flagsmith-task-processor": ops.pebble.Layer(
                {"services": {SERVICE: {"command": "x", "override": "replace"}}}
            )
        },
        service_statuses={SERVICE: ops.pebble.ServiceStatus.ACTIVE},
    )
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("task-processor-peers"), _api_relation(complete=False)},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.WaitingStatus)

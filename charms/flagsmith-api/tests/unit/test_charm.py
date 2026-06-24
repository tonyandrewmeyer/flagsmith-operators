# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Flagsmith API charm."""

import ops
import pytest
from ops import testing

from charm import DATABASE_NAME, FlagsmithApiCharm

CONTAINER = "flagsmith-api"
SERVICE = "flagsmith-api"


@pytest.fixture
def ctx():
    return testing.Context(FlagsmithApiCharm)


def _db_relation(complete: bool = True) -> testing.Relation:
    data = {}
    if complete:
        data = {
            "endpoints": "postgresql-k8s-primary:5432",
            "username": "relation-1",
            "password": "supersecret",
            "database": DATABASE_NAME,
        }
    return testing.Relation(
        endpoint="database",
        interface="postgresql_client",
        remote_app_name="postgresql-k8s",
        remote_app_data=data,
    )


def _execs() -> set:
    """Mock workload execs that the reconcile path invokes."""
    return {
        testing.Exec(["/app/scripts/run-docker.sh", "migrate"], return_code=0, stdout="ok"),
        testing.Exec(["cat", "/app/CHANGELOG.md"], return_code=0, stdout="2.245.0\n"),
    }


def test_blocked_without_database(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("flagsmith-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert state_out.unit_status == ops.BlockedStatus("missing required relation: database")


def test_waiting_when_db_incomplete(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation(complete=False)},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.WaitingStatus)


def test_active_and_service_started_with_db(ctx):
    container = testing.Container(CONTAINER, can_connect=True, execs=_execs())
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    # The leader runs migrations via container.exec; register a mock handler.
    state_out = ctx.run(
        ctx.on.pebble_ready(container),
        state_in,
    )
    container_out = state_out.get_container(CONTAINER)
    assert SERVICE in container_out.plan.services
    svc = container_out.plan.services[SERVICE]
    assert svc.command.endswith("serve")
    env = svc.environment
    assert env["DATABASE_URL"].startswith("postgresql://relation-1:supersecret@")
    assert env["DJANGO_SECRET_KEY"]  # generated into peer data
    assert state_out.unit_status == ops.ActiveStatus()


def test_leader_generates_secret_key_in_peer_data(ctx):
    peer = testing.PeerRelation("flagsmith-peers")
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=True, execs=_execs())},
        relations={peer, _db_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    peer_out = state_out.get_relation(peer.id)
    assert peer_out.local_app_data.get("secret-key")


def test_non_leader_waits_for_secret_key(ctx):
    # Non-leader with empty peer data cannot generate a key -> waiting.
    state_in = testing.State(
        leader=False,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.WaitingStatus)


def test_non_leader_uses_existing_peer_secret(ctx):
    peer = testing.PeerRelation(
        "flagsmith-peers", local_app_data={"secret-key": "shared-key-from-leader"}
    )
    container = testing.Container(CONTAINER, can_connect=True, execs=_execs())
    state_in = testing.State(
        leader=False,
        containers={container},
        relations={peer, _db_relation()},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    svc = state_out.get_container(CONTAINER).plan.services[SERVICE]
    assert svc.environment["DJANGO_SECRET_KEY"] == "shared-key-from-leader"
    assert state_out.unit_status == ops.ActiveStatus()


def test_invalid_extra_env_blocks(ctx):
    state_in = testing.State(
        leader=True,
        config={"extra-env": "this is not json"},
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "extra-env" in state_out.unit_status.message


def test_container_not_ready_is_maintenance(ctx):
    state_in = testing.State(
        leader=True,
        containers={testing.Container(CONTAINER, can_connect=False)},
        relations={testing.PeerRelation("flagsmith-peers")},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert isinstance(state_out.unit_status, ops.MaintenanceStatus)


def test_publishes_api_url_to_siblings(ctx):
    api_rel = testing.Relation(endpoint="flagsmith-api", interface="flagsmith_api")
    container = testing.Container(CONTAINER, can_connect=True, execs=_execs())
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation(), api_rel},
    )
    state_out = ctx.run(ctx.on.relation_joined(api_rel), state_in)
    rel_out = state_out.get_relation(api_rel.id)
    assert "api-url" in rel_out.local_app_data
    assert rel_out.local_app_data["api-url"].startswith("http://flagsmith-api")


def _action_execs() -> set:
    return {
        testing.Exec(["/app/scripts/run-docker.sh", "migrate"], return_code=0, stdout="ok"),
        testing.Exec(["cat", "/app/CHANGELOG.md"], return_code=0, stdout="2.245.0\n"),
        testing.Exec(["/app/scripts/run-docker.sh", "shell"], return_code=0, stdout="created\n"),
    }


def test_run_migrations_action(ctx):
    container = testing.Container(CONTAINER, can_connect=True, execs=_execs())
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    ctx.run(ctx.on.action("run-migrations"), state_in)
    assert ctx.action_results == {"result": "migrations applied"}


def test_run_migrations_action_non_leader_fails(ctx):
    state_in = testing.State(
        leader=False,
        containers={testing.Container(CONTAINER, can_connect=True)},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("run-migrations"), state_in)


def test_create_admin_user_generates_password(ctx):
    container = testing.Container(CONTAINER, can_connect=True, execs=_action_execs())
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={testing.PeerRelation("flagsmith-peers"), _db_relation()},
    )
    ctx.run(ctx.on.action("create-admin-user", params={"email": "a@b.co"}), state_in)
    assert ctx.action_results["email"] == "a@b.co"
    assert ctx.action_results["password"]


def test_rotate_secret_key_action(ctx):
    peer = testing.PeerRelation("flagsmith-peers", local_app_data={"secret-key": "old"})
    container = testing.Container(CONTAINER, can_connect=True, execs=_execs())
    state_in = testing.State(
        leader=True,
        containers={container},
        relations={peer, _db_relation()},
    )
    state_out = ctx.run(ctx.on.action("rotate-secret-key"), state_in)
    peer_out = state_out.get_relation(peer.id)
    assert peer_out.local_app_data["secret-key"] != "old"

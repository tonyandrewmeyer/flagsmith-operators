# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.
#
# Integration tests use Jubilant. See https://documentation.ubuntu.com/jubilant/

import logging
import pathlib

import jubilant
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(pathlib.Path("charmcraft.yaml").read_text())
APP = "flagsmith-api"
POSTGRES = "postgresql-k8s"


def test_deploy_and_integrate(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the API with PostgreSQL and confirm it reaches active."""
    resources = {"flagsmith-image": METADATA["resources"]["flagsmith-image"]["upstream-source"]}
    juju.deploy(charm.resolve(), app=APP, resources=resources)
    juju.deploy(POSTGRES, channel="14/stable", trust=True)

    # Before the database relation, the charm must be blocked on it.
    juju.wait(lambda status: status.apps[APP].is_blocked, timeout=600)

    juju.integrate(f"{APP}:database", POSTGRES)
    juju.wait(jubilant.all_active, timeout=900)


def test_workload_version_is_set(juju: jubilant.Juju):
    """The charm should report the running Flagsmith version."""
    version = juju.status().apps[APP].version
    assert version, "expected a workload version to be set"


def test_create_admin_user_action(juju: jubilant.Juju):
    """The create-admin-user action returns a generated password."""
    leader = next(unit for unit, info in juju.status().apps[APP].units.items() if info.leader)
    result = juju.run(leader, "create-admin-user", {"email": "admin@example.com"})
    assert result.results.get("password")
    assert result.results.get("email") == "admin@example.com"


def test_scale_out(juju: jubilant.Juju):
    """Scaling the stateless API to two units stays active."""
    juju.cli("scale-application", APP, "2")
    juju.wait(jubilant.all_active, timeout=600)
    assert len(juju.status().apps[APP].units) == 2

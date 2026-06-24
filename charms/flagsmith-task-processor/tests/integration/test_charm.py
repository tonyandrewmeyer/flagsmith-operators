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
APP = "flagsmith-task-processor"
API = "flagsmith-api"
POSTGRES = "postgresql-k8s"


def test_deploy_and_integrate(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the task processor with the API + PostgreSQL and reach active."""
    resources = {
        "flagsmith-image": METADATA["resources"]["flagsmith-image"]["upstream-source"]
    }
    juju.deploy(charm.resolve(), app=APP, resources=resources)
    juju.deploy(API, channel="latest/edge", resources={"flagsmith-image": resources["flagsmith-image"]})
    juju.deploy(POSTGRES, channel="14/stable", trust=True)

    # Blocked until related to the API.
    juju.wait(lambda status: status.apps[APP].is_blocked, timeout=600)

    juju.integrate(API, POSTGRES)
    juju.integrate(f"{APP}:flagsmith-api", f"{API}:flagsmith-api")
    juju.wait(jubilant.all_active, timeout=900)

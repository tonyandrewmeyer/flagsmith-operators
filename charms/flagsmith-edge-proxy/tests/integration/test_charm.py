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
APP = "flagsmith-edge-proxy"

PAIRS = '[{"server_side_key": "ser.example", "client_side_key": "example"}]'


def test_deploy(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the proxy; without an API URL it should block, then unblock."""
    resources = {"flagsmith-image": METADATA["resources"]["flagsmith-image"]["upstream-source"]}
    juju.deploy(charm.resolve(), app=APP, resources=resources)

    # No API URL and no key pairs -> blocked.
    juju.wait(lambda status: status.apps[APP].is_blocked, timeout=600)

    # Provide an external API URL and key pairs; with both set it should become
    # active (the proxy serves regardless of API reachability for liveness).
    juju.config(APP, {"api-url": "https://edge.flagsmith.com", "environment-key-pairs": PAIRS})
    juju.wait(jubilant.all_active, timeout=600)

#!/usr/bin/env python3
# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Charmed operator for the Flagsmith Edge Proxy.

A holistic (reconciler) charm operating the ``flagsmith/edge-proxy`` image. The
proxy caches environment documents fetched from the Flagsmith API and serves
flag evaluations to SDKs at low latency. It learns the API URL from the
``flagsmith-api`` relation (or an explicit ``api-url`` config override) and is
configured with one or more environment key pairs.
"""

import logging
import typing

import ops
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

import edge_proxy
from edge_proxy import CONTAINER_NAME, PORT, SERVICE_NAME, EdgeProxyConfig

logger = logging.getLogger(__name__)

API_RELATION = "flagsmith-api"


class FlagsmithEdgeProxyCharm(ops.CharmBase):
    """Operate the Flagsmith Edge Proxy workload."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container(CONTAINER_NAME)

        self.ingress = IngressPerAppRequirer(
            self, relation_name="ingress", port=PORT, strip_prefix=False
        )
        self.log_forwarder = LogForwarder(self, relation_name="logging")

        for event in (
            self.on[CONTAINER_NAME].pebble_ready,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.start,
            self.on[API_RELATION].relation_changed,
            self.on[API_RELATION].relation_broken,
            self.ingress.on.ready,
            self.ingress.on.revoked,
        ):
            framework.observe(event, self._reconcile)

        framework.observe(self.on.collect_unit_status, self._on_collect_status)

    # ------------------------------------------------------------------ #
    # Reconciliation
    # ------------------------------------------------------------------ #
    def _reconcile(self, _event: ops.EventBase) -> None:
        if not self.container.can_connect():
            logger.debug("workload container not ready; deferring to next event")
            return
        try:
            config = self._build_config()
        except ValueError:
            logger.exception("invalid configuration; not (re)starting workload")
            return
        if not config.is_ready:
            if self.container.get_services(SERVICE_NAME):
                self.container.stop(SERVICE_NAME)
            return
        layer = ops.pebble.Layer(typing.cast(ops.pebble.LayerDict, edge_proxy.build_layer(config)))
        self.container.add_layer(SERVICE_NAME, layer, combine=True)
        self.container.replan()

    def _build_config(self) -> EdgeProxyConfig:
        return EdgeProxyConfig(
            api_url=self._api_url(),
            environment_key_pairs=typing.cast(str, self.config["environment-key-pairs"]),
            api_poll_frequency_seconds=int(
                typing.cast(int, self.config["api-poll-frequency-seconds"])
            ),
            api_poll_timeout_seconds=int(
                typing.cast(int, self.config["api-poll-timeout-seconds"])
            ),
            allow_origins=typing.cast(str, self.config["allow-origins"]),
            web_concurrency=int(typing.cast(int, self.config["web-concurrency"])),
            log_level=typing.cast(str, self.config["log-level"]).upper(),
            extra_env=edge_proxy.parse_extra_env(typing.cast(str, self.config["extra-env"])),
        )

    def _api_url(self) -> str | None:
        override = typing.cast(str, self.config.get("api-url") or "").strip()
        if override:
            return edge_proxy.api_evaluation_url(override)
        relation = self.model.get_relation(API_RELATION)
        if relation and relation.app:
            return edge_proxy.api_evaluation_url(relation.data[relation.app].get("api-url"))
        return None

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        if not self.container.can_connect():
            event.add_status(ops.MaintenanceStatus("waiting for workload container"))
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            event.add_status(ops.BlockedStatus(f"invalid config: {exc}"))
            return
        if not config.api_url:
            event.add_status(
                ops.BlockedStatus("missing API URL: integrate with flagsmith-api or set api-url")
            )
            return
        try:
            pairs = edge_proxy.validate_environment_key_pairs(config.environment_key_pairs)
        except ValueError as exc:
            event.add_status(ops.BlockedStatus(f"invalid environment-key-pairs: {exc}"))
            return
        if not pairs:
            event.add_status(ops.BlockedStatus("configure environment-key-pairs"))
            return
        services = self.container.get_services(SERVICE_NAME)
        if not services or not list(services.values())[0].is_running():
            event.add_status(ops.WaitingStatus("starting Flagsmith edge proxy"))
            return
        event.add_status(ops.ActiveStatus())


if __name__ == "__main__":  # pragma: nocover
    ops.main(FlagsmithEdgeProxyCharm)

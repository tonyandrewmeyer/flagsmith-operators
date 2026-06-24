#!/usr/bin/env python3
# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Charmed operator for the Flagsmith dashboard frontend.

A holistic (reconciler) charm operating the dedicated ``flagsmith-frontend``
image. It learns the Flagsmith API base URL from the ``flagsmith-api`` relation
(or an explicit ``api-url`` config override) and serves the management
dashboard. Pair it with ``flagsmith-api`` configured with
``enable-admin-dashboard=false`` so a single dashboard is served.
"""

import logging
import typing

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

import frontend
from frontend import CONTAINER_NAME, PORT, SERVICE_NAME, FrontendConfig

logger = logging.getLogger(__name__)

API_RELATION = "flagsmith-api"


class FlagsmithFrontendCharm(ops.CharmBase):
    """Operate the Flagsmith dashboard frontend workload."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container(CONTAINER_NAME)

        self.ingress = IngressPerAppRequirer(
            self, relation_name="ingress", port=PORT, strip_prefix=False
        )
        self.dashboards = GrafanaDashboardProvider(self, relation_name="grafana-dashboard")
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
        layer = ops.pebble.Layer(typing.cast(ops.pebble.LayerDict, frontend.build_layer(config)))
        self.container.add_layer(SERVICE_NAME, layer, combine=True)
        self.container.replan()

    def _build_config(self) -> FrontendConfig:
        return FrontendConfig(
            api_url=self._api_url(),
            log_level=typing.cast(str, self.config["log-level"]).upper(),
            disable_analytics=bool(self.config["disable-analytics"]),
            extra_env=frontend.parse_extra_env(typing.cast(str, self.config["extra-env"])),
        )

    def _api_url(self) -> str | None:
        """Resolve the API base URL from config override or the relation."""
        override = typing.cast(str, self.config.get("api-url") or "").strip()
        if override:
            return frontend.api_base_url(override)
        relation = self.model.get_relation(API_RELATION)
        if relation and relation.app:
            return frontend.api_base_url(relation.data[relation.app].get("api-url"))
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
        if not config.is_ready:
            event.add_status(
                ops.BlockedStatus("missing API URL: integrate with flagsmith-api or set api-url")
            )
            return
        services = self.container.get_services(SERVICE_NAME)
        if not services or not list(services.values())[0].is_running():
            event.add_status(ops.WaitingStatus("starting Flagsmith frontend"))
            return
        event.add_status(ops.ActiveStatus())


if __name__ == "__main__":  # pragma: nocover
    ops.main(FlagsmithFrontendCharm)

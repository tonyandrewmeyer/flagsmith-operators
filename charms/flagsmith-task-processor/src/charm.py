#!/usr/bin/env python3
# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Charmed operator for the Flagsmith task processor.

A holistic (reconciler) charm. It runs the ``flagsmith/flagsmith`` image with
the ``run-task-processor`` subcommand and consumes the shared database URL and
Django secret key published by the flagsmith-api charm over the ``flagsmith-api``
relation. The task processor is stateless and horizontally scalable; the API
charm owns database migrations, so there is no leader special-casing here.
"""

import logging
import typing

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer

import task_processor
from task_processor import (
    CONTAINER_NAME,
    METRICS_PATH,
    PORT,
    SERVICE_NAME,
    TaskProcessorConfig,
)

logger = logging.getLogger(__name__)

API_RELATION = "flagsmith-api"


@trace_charm(
    tracing_endpoint="charm_tracing_endpoint",
    extra_types=(MetricsEndpointProvider,),
)
class FlagsmithTaskProcessorCharm(ops.CharmBase):
    """Operate the Flagsmith task processor workload."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container(CONTAINER_NAME)

        self.metrics = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [{"targets": [f"*:{PORT}"]}],
                }
            ],
        )
        self.dashboards = GrafanaDashboardProvider(self, relation_name="grafana-dashboard")
        self.log_forwarder = LogForwarder(self, relation_name="logging")
        self.tracing = TracingEndpointRequirer(
            self, relation_name="tracing", protocols=["otlp_http"]
        )

        for event in (
            self.on[CONTAINER_NAME].pebble_ready,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.start,
            self.on[API_RELATION].relation_changed,
            self.on[API_RELATION].relation_broken,
            self.tracing.on.endpoint_changed,
            self.tracing.on.endpoint_removed,
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
        layer = ops.pebble.Layer(
            typing.cast(ops.pebble.LayerDict, task_processor.build_layer(config))
        )
        self.container.add_layer(SERVICE_NAME, layer, combine=True)
        self.container.replan()

    def _build_config(self) -> TaskProcessorConfig:
        api = self._api_relation_data()
        return TaskProcessorConfig(
            database_url=api.get("database-url"),
            secret_key=api.get("secret-key"),
            log_level=typing.cast(str, self.config["log-level"]).upper(),
            num_threads=int(typing.cast(int, self.config["num-threads"])),
            sleep_interval_ms=int(typing.cast(int, self.config["sleep-interval-ms"])),
            queue_pop_size=int(typing.cast(int, self.config["queue-pop-size"])),
            grace_period_ms=int(typing.cast(int, self.config["grace-period-ms"])),
            prometheus_enabled=True,
            tracing_endpoint=self._tracing_endpoint(),
            extra_env=task_processor.parse_extra_env(typing.cast(str, self.config["extra-env"])),
        )

    def _api_relation_data(self) -> dict[str, str]:
        relation = self.model.get_relation(API_RELATION)
        if not relation or not relation.app:
            return {}
        return dict(relation.data[relation.app])

    def _tracing_endpoint(self) -> str | None:
        if self.tracing.is_ready():
            return self.tracing.get_endpoint("otlp_http")
        return None

    @property
    def charm_tracing_endpoint(self) -> str | None:
        """OTLP endpoint for tracing the charm code itself."""
        if self.tracing.is_ready():
            return self.tracing.get_endpoint("otlp_http")
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
        if not self.model.get_relation(API_RELATION):
            event.add_status(ops.BlockedStatus("missing required relation: flagsmith-api"))
            return
        if not config.is_ready:
            event.add_status(
                ops.WaitingStatus("waiting for database and secret from flagsmith-api")
            )
            return
        services = self.container.get_services(SERVICE_NAME)
        if not services or not list(services.values())[0].is_running():
            event.add_status(ops.WaitingStatus("starting Flagsmith task processor"))
            return
        event.add_status(ops.ActiveStatus())


if __name__ == "__main__":  # pragma: nocover
    ops.main(FlagsmithTaskProcessorCharm)

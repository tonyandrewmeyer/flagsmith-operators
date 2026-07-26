#!/usr/bin/env python3
# Copyright 2026 Flagsmith Charmers
# See LICENSE file for licensing details.

"""Charmed operator for the Flagsmith API.

This is a *holistic* (reconciler) charm: every lifecycle and relation event is
observed, but each handler simply calls :meth:`FlagsmithApiCharm._reconcile`,
which re-derives the complete desired state of the workload from config and
relation data and applies it idempotently. Unit status is computed separately
in ``collect-unit-status`` so it always reflects current reality.
"""

import logging
import secrets
import typing

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.redis_k8s.v0.redis import RedisRequires
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

import flagsmith
from flagsmith import (
    API_PORT,
    CONTAINER_NAME,
    METRICS_PATH,
    SERVICE_NAME,
    FlagsmithConfig,
)

logger = logging.getLogger(__name__)

PEER_RELATION = "flagsmith-peers"
DATABASE_RELATION = "database"
DATABASE_NAME = "flagsmith"
SECRET_KEY_FIELD = "secret-key"


@trace_charm(
    tracing_endpoint="charm_tracing_endpoint",
    extra_types=(
        DatabaseRequires,
        IngressPerAppRequirer,
        MetricsEndpointProvider,
    ),
)
class FlagsmithApiCharm(ops.CharmBase):
    """Operate the Flagsmith API workload."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container(CONTAINER_NAME)

        # --- Integrations ------------------------------------------------
        self.database = DatabaseRequires(
            self,
            relation_name=DATABASE_RELATION,
            database_name=DATABASE_NAME,
        )
        self.redis = RedisRequires(self, relation_name="redis")
        self.ingress = IngressPerAppRequirer(
            self,
            relation_name="ingress",
            port=API_PORT,
            strip_prefix=False,
        )
        self.metrics = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {"metrics_path": METRICS_PATH, "static_configs": [{"targets": [f"*:{API_PORT}"]}]}
            ],
        )
        self.dashboards = GrafanaDashboardProvider(self, relation_name="grafana-dashboard")
        self.log_forwarder = LogForwarder(self, relation_name="logging")
        self.tracing = TracingEndpointRequirer(
            self, relation_name="tracing", protocols=["otlp_http"]
        )

        # --- Event observation -------------------------------------------
        # Every meaningful event funnels into the single reconciler.
        for event in (
            self.on[CONTAINER_NAME].pebble_ready,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.start,
            self.on.secret_changed,
            self.on[PEER_RELATION].relation_changed,
            self.on[PEER_RELATION].relation_created,
            self.database.on.database_created,
            self.database.on.endpoints_changed,
            self.on["redis"].relation_changed,
            self.on["redis"].relation_broken,
            self.on["database"].relation_broken,
            self.ingress.on.ready,
            self.ingress.on.revoked,
            self.tracing.on.endpoint_changed,
            self.tracing.on.endpoint_removed,
            self.on["flagsmith-api"].relation_changed,
            self.on["flagsmith-api"].relation_joined,
        ):
            framework.observe(event, self._reconcile)

        framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # Actions.
        framework.observe(self.on.create_admin_user_action, self._on_create_admin_user)
        framework.observe(self.on.rotate_secret_key_action, self._on_rotate_secret_key)
        framework.observe(self.on.run_migrations_action, self._on_run_migrations)
        framework.observe(
            self.on.dump_environment_document_action, self._on_dump_environment_document
        )

    # ------------------------------------------------------------------ #
    # Reconciliation
    # ------------------------------------------------------------------ #
    def _reconcile(self, _event: ops.EventBase) -> None:
        """Re-derive and apply the complete desired workload state."""
        if not self.container.can_connect():
            logger.debug("workload container not ready; deferring reconcile to next event")
            return

        try:
            config = self._build_config()
        except ValueError:
            logger.exception("invalid configuration; not (re)starting workload")
            return

        if not config.is_ready:
            # Stop the service so we don't run with a stale/partial config.
            if self.container.get_services(SERVICE_NAME):
                self.container.stop(SERVICE_NAME)
            self._publish_api_relation(config)
            return

        # Leader owns the schema: run migrations before (re)starting workers.
        if self.unit.is_leader():
            self._run_migrations(config)

        layer = ops.pebble.Layer(typing.cast(ops.pebble.LayerDict, flagsmith.build_layer(config)))
        self.container.add_layer(SERVICE_NAME, layer, combine=True)
        self.container.replan()

        version = self._workload_version()
        if version:
            self.unit.set_workload_version(version)

        self._publish_api_relation(config)

    def _build_config(self) -> FlagsmithConfig:
        """Assemble the desired configuration from config and relations."""
        extra_env = flagsmith.parse_extra_env(typing.cast(str, self.config["extra-env"]))
        return FlagsmithConfig(
            database_url=self._database_url(),
            redis_url=self.redis.url if self._redis_related() else None,
            secret_key=self._secret_key(),
            domain=self._external_host(),
            allowed_hosts=typing.cast(str, self.config["allowed-hosts"]),
            log_level=typing.cast(str, self.config["log-level"]).upper(),
            gunicorn_workers=int(typing.cast(int, self.config["gunicorn-workers"])),
            gunicorn_threads=int(typing.cast(int, self.config["gunicorn-threads"])),
            gunicorn_timeout=int(typing.cast(int, self.config["gunicorn-timeout"])),
            serve_frontend=bool(self.config["enable-admin-dashboard"]),
            prevent_signup=bool(self.config["prevent-signup"]),
            enable_telemetry=bool(self.config["enable-telemetry"]),
            use_postgres_for_analytics=bool(self.config["use-postgres-for-analytics"]),
            prometheus_enabled=True,
            task_processor_enabled=typing.cast(str, self.config["task-run-method"])
            == "TASK_PROCESSOR",
            tracing_endpoint=self._tracing_endpoint(),
            extra_env=extra_env,
        )

    # ------------------------------------------------------------------ #
    # Datastore plumbing
    # ------------------------------------------------------------------ #
    def _database_url(self) -> str | None:
        relation = self.model.get_relation(DATABASE_RELATION)
        if not relation:
            return None
        data = self.database.fetch_relation_data().get(relation.id, {})
        endpoints = data.get("endpoints")
        username = data.get("username")
        password = data.get("password")
        if not (endpoints and username and password):
            return None
        host, _, port = endpoints.split(",")[0].partition(":")
        return flagsmith.postgresql_uri(
            host=host,
            port=port or "5432",
            username=username,
            password=password,
            database=data.get("database", DATABASE_NAME),
        )

    def _redis_related(self) -> bool:
        return self.model.get_relation("redis") is not None

    # ------------------------------------------------------------------ #
    # Secret-key management
    # ------------------------------------------------------------------ #
    def _secret_key(self) -> str | None:
        """Resolve the Django secret key from a user secret or peer data.

        Precedence: an explicitly configured Juju user secret, else a random
        key generated once and persisted in peer relation data so it is shared
        by all units and stable across restarts.
        """
        configured = self.config.get("secret-key")
        if configured:
            try:
                secret = self.model.get_secret(id=typing.cast(str, configured))
                content = secret.get_content(refresh=True)
                return content.get(SECRET_KEY_FIELD) or next(iter(content.values()), None)
            except (ops.SecretNotFoundError, ops.ModelError):
                logger.warning("configured secret-key could not be read")
                return None

        peers = self.model.get_relation(PEER_RELATION)
        if not peers:
            return None
        existing = peers.data[self.app].get(SECRET_KEY_FIELD)
        if existing:
            return existing
        if self.unit.is_leader():
            generated = secrets.token_hex(32)
            peers.data[self.app][SECRET_KEY_FIELD] = generated
            return generated
        return None

    # ------------------------------------------------------------------ #
    # Ingress / external addressing
    # ------------------------------------------------------------------ #
    def _external_host(self) -> str | None:
        if self.ingress.url:
            return self.ingress.url.split("://", 1)[-1].rstrip("/")
        return None

    def _tracing_endpoint(self) -> str | None:
        if self.tracing.is_ready():
            return self.tracing.get_endpoint("otlp_http")
        return None

    @property
    def charm_tracing_endpoint(self) -> str | None:
        """OTLP endpoint for tracing the charm code itself (charm_tracing)."""
        if self.tracing.is_ready():
            return self.tracing.get_endpoint("otlp_http")
        return None

    # ------------------------------------------------------------------ #
    # Inter-charm relation
    # ------------------------------------------------------------------ #
    def _publish_api_relation(self, config: FlagsmithConfig) -> None:
        """Share the API's internal URL and Django secret with sibling charms."""
        if not self.unit.is_leader():
            return
        internal_url = f"http://{self.app.name}.{self.model.name}.svc.cluster.local:{API_PORT}"
        for relation in self.model.relations.get("flagsmith-api", []):
            relation.data[self.app]["api-url"] = internal_url
            if config.secret_key:
                relation.data[self.app][SECRET_KEY_FIELD] = config.secret_key
            if config.database_url:
                relation.data[self.app]["database-url"] = config.database_url

    # ------------------------------------------------------------------ #
    # Migrations
    # ------------------------------------------------------------------ #
    def _run_migrations(self, config: FlagsmithConfig) -> None:
        """Run Django migrations (idempotent). Leader only."""
        env = flagsmith.build_environment(config)
        logger.info("running database migrations")
        process = self.container.exec(
            [flagsmith.ENTRYPOINT, "migrate"],
            environment=env,
            working_dir=flagsmith.WORKING_DIR,
            timeout=600,
        )
        try:
            process.wait_output()
            logger.info("database migrations complete")
        except ops.pebble.ExecError as exc:
            logger.error("migrations failed (exit %s): %s", exc.exit_code, exc.stderr)
            raise

    def _workload_version(self) -> str | None:
        try:
            process = self.container.exec(
                ["cat", "/app/CHANGELOG.md"],
                timeout=10,
            )
            out, _ = process.wait_output()
            for line in out.splitlines():
                line = line.strip().lstrip("#").strip()
                if line and line[0].isdigit():
                    return line.split()[0]
        except (ops.pebble.ExecError, ops.pebble.ChangeError, FileNotFoundError):
            pass
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
        if not self.model.get_relation(DATABASE_RELATION):
            event.add_status(ops.BlockedStatus("missing required relation: database"))
            return
        if not config.database_url:
            event.add_status(ops.WaitingStatus("waiting for database credentials"))
            return
        if not config.secret_key:
            event.add_status(ops.WaitingStatus("waiting for secret-key (peer or user secret)"))
            return
        services = self.container.get_services(SERVICE_NAME)
        if not services or not next(iter(services.values())).is_running():
            event.add_status(ops.WaitingStatus("starting Flagsmith API"))
            return
        event.add_status(ops.ActiveStatus())

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _require_leader(self, event: ops.ActionEvent) -> bool:
        if not self.unit.is_leader():
            event.fail("This action must be run on the leader unit.")
            return False
        if not self.container.can_connect():
            event.fail("Workload container is not ready.")
            return False
        return True

    def _on_create_admin_user(self, event: ops.ActionEvent) -> None:
        if not self._require_leader(event):
            return
        config = self._build_config()
        if not config.is_ready:
            event.fail("Database and secret-key must be configured first.")
            return
        email = event.params["email"]
        password = event.params.get("password") or secrets.token_urlsafe(16)
        name = event.params.get("name", "Administrator")
        first, _, last = name.partition(" ")
        script = (
            "import os;"
            "from users.models import FFAdminUser;"
            "email = os.environ['FS_ADMIN_EMAIL'];"
            "password = os.environ['FS_ADMIN_PASSWORD'];"
            "first = os.environ['FS_ADMIN_FIRST'];"
            "last = os.environ['FS_ADMIN_LAST'];"
            "u, created = FFAdminUser.objects.get_or_create(email=email,"
            "defaults={'first_name': first, 'last_name': last});"
            "u.is_superuser = True; u.is_staff = True;"
            "u.first_name = first; u.last_name = last or u.last_name;"
            "u.set_password(password); u.save();"
            "print('created' if created else 'updated')"
        )
        env = flagsmith.build_environment(config)
        env.update(
            {
                "FS_ADMIN_EMAIL": email,
                "FS_ADMIN_PASSWORD": password,
                "FS_ADMIN_FIRST": first,
                "FS_ADMIN_LAST": last,
            }
        )
        try:
            proc = self.container.exec(
                [flagsmith.ENTRYPOINT, "shell", "-c", script],
                environment=env,
                working_dir=flagsmith.WORKING_DIR,
                timeout=120,
            )
            out, _ = proc.wait_output()
            event.set_results(
                {"result": out.strip() or "ok", "email": email, "password": password}
            )
        except ops.pebble.ExecError as exc:
            event.fail(f"Failed to create admin user: {exc.stderr}")

    def _on_rotate_secret_key(self, event: ops.ActionEvent) -> None:
        if not self._require_leader(event):
            return
        if self.config.get("secret-key"):
            event.set_results(
                {
                    "result": "A user secret is configured for secret-key. Rotate it with "
                    "'juju update-secret', then the charm will pick up the change.",
                }
            )
            return
        peers = self.model.get_relation(PEER_RELATION)
        if not peers:
            event.fail("Peer relation not ready.")
            return
        peers.data[self.app][SECRET_KEY_FIELD] = secrets.token_hex(32)
        self._reconcile(event)
        event.set_results({"result": "secret-key rotated and API restarted"})

    def _on_run_migrations(self, event: ops.ActionEvent) -> None:
        if not self._require_leader(event):
            return
        config = self._build_config()
        if not config.is_ready:
            event.fail("Database and secret-key must be configured first.")
            return
        try:
            self._run_migrations(config)
            event.set_results({"result": "migrations applied"})
        except ops.pebble.ExecError as exc:
            event.fail(f"Migrations failed: {exc.stderr}")

    def _on_dump_environment_document(self, event: ops.ActionEvent) -> None:
        if not self._require_leader(event):
            return
        key = event.params["environment-api-key"]
        config = self._build_config()
        env = flagsmith.build_environment(config)
        env["FS_ENV_API_KEY"] = key
        script = (
            "import os, json;"
            "from environments.models import Environment;"
            "from environments.dynamodb.types import map_environment_to_environment_document;"
            "e = Environment.objects.get(api_key=os.environ['FS_ENV_API_KEY']);"
            "print(json.dumps(map_environment_to_environment_document(e), default=str))"
        )
        try:
            proc = self.container.exec(
                [flagsmith.ENTRYPOINT, "shell", "-c", script],
                environment=env,
                working_dir=flagsmith.WORKING_DIR,
                timeout=60,
            )
            out, _ = proc.wait_output()
            event.set_results({"document": out.strip()})
        except ops.pebble.ExecError as exc:
            event.fail(f"Failed to dump environment document: {exc.stderr}")


if __name__ == "__main__":  # pragma: nocover
    ops.main(FlagsmithApiCharm)

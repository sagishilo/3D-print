"""Integrations agent (Wave 2 / W2-0): runs every connector (stores, invoicing, couriers, alert channels) and keeps
the notifications outbox.

Connectors (connectors.py) are seeded into integrations.connectors OFF and in FAKE mode. Every TICK_S the agent runs
the enabled connectors that are due: one sync (outbound only, with a timeout), its records mapped in one transaction
(integrations.mappings: created / updated / unchanged, so a repeated sync never duplicates), its result in
integrations.runs and sync_state. A failure waits longer each time (framework.backoff_s); the third in a row tells
an owner (notify.outbox). evt.integrations.sync.finished reports each sync.

Secrets come from integrations.env (the container's environment, or INTEGRATIONS_ENV_FILE): only "set / missing"
per declared key is ever stored. Live mode refuses to run while a required secret is missing.

Notifications: critical platform events (alerts.py) and evt.notify.requested from any agent land once in
notify.outbox (dedupe_key). W2-D delivers them; until then they show on the dashboard.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.integrations import alerts
from printfarm.agents.integrations.connectors import REGISTRY
from printfarm.agents.integrations.framework import (
    FAILURES_TO_NOTIFY,
    Connector,
    SyncContext,
    SyncError,
    diff,
    is_due,
    live_blocked,
    next_run,
    read_env_file,
    secrets_status,
)
from printfarm.contracts.messages import (
    ConfigureConnector,
    ConnectorChanged,
    ConnectorSynced,
    NotificationQueued,
    NotificationRequested,
    RunConnector,
)

TICK_S = 5
SYNC_TIMEOUT_S = 120
RUNS_KEPT = 200                  # per connector


class IntegrationsAgent(Agent):
    name = "integrations"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registry: dict[str, Connector] = dict(REGISTRY)
        self._running: set[str] = set()

    # ------------------------------------------------------------ secrets

    def secret_values(self, c: Connector) -> dict[str, str]:
        """This connector's secrets: from INTEGRATIONS_ENV_FILE (if set) and the environment (integrations.env)."""
        path = os.getenv("INTEGRATIONS_ENV_FILE", "")
        file_values = read_env_file(path) if path else {}
        out = {}
        for s in c.secrets:
            v = os.getenv(s.key) or file_values.get(s.key)
            if v:
                out[s.key] = v
        return out

    # ------------------------------------------------------------ start and schedule

    async def on_start(self) -> None:
        async with self.db.tx() as conn:
            for c in self.registry.values():
                status = Jsonb(secrets_status(c.secrets, self.secret_values(c)))
                await conn.execute(
                    "INSERT INTO integrations.connectors (connector_id, kind, label, interval_s, secrets_status) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (connector_id) DO UPDATE SET kind = EXCLUDED.kind, "
                    "label = EXCLUDED.label, secrets_status = EXCLUDED.secrets_status",
                    (c.id, c.kind, c.label, c.default_interval_s, status))
                await conn.execute("INSERT INTO integrations.sync_state (connector_id) VALUES (%s) ON CONFLICT DO NOTHING",
                                   (c.id,))
        self.spawn(self._loop())

    def heartbeat_info(self) -> dict:
        return {"connectors": len(self.registry), "running": sorted(self._running)}

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(UTC))
            except Exception:  # noqa: BLE001 - one bad tick must not stop the scheduler
                self.log.exception("integrations tick failed")
            await asyncio.sleep(TICK_S)

    async def tick(self, now: datetime) -> list[str]:
        """Run every enabled connector that is due. Returns the ids it ran."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT c.connector_id, c.enabled, s.next_run_at FROM integrations.connectors c "
                "JOIN integrations.sync_state s USING (connector_id)")).fetchall()
        due = [r["connector_id"] for r in rows if is_due(r["enabled"], r["next_run_at"], now)
               and r["connector_id"] in self.registry and r["connector_id"] not in self._running]
        for cid in due:
            await self.run(cid, trigger="schedule")
        return due

    # ------------------------------------------------------------ one sync

    async def run(self, connector_id: str, *, trigger: str, cause=None) -> ConnectorSynced | None:
        c = self.registry.get(connector_id)
        if c is None or connector_id in self._running:
            return None
        self._running.add(connector_id)
        try:
            return await self._run(c, trigger, cause)
        finally:
            self._running.discard(connector_id)

    async def _run(self, c: Connector, trigger: str, cause) -> ConnectorSynced:
        now = datetime.now(UTC)
        async with self.db.tx() as conn:
            cfg = await (await conn.execute(
                "SELECT c.*, s.cursor, s.consecutive_failures FROM integrations.connectors c "
                "JOIN integrations.sync_state s USING (connector_id) WHERE connector_id = %s", (c.id,))).fetchone()
            run_id = (await (await conn.execute(
                "INSERT INTO integrations.runs (connector_id, trigger) VALUES (%s, %s) RETURNING id",
                (c.id, trigger))).fetchone())["id"]
        values = self.secret_values(c)
        ctx = SyncContext(mode=cfg["mode"], cursor=cfg["cursor"] or {}, settings=cfg["settings"] or {}, secrets=values,
                          now=now, log=lambda m: self.log.info("%s: %s", c.id, m))
        error, result = "", None
        try:
            missing = live_blocked(c.secrets, values) if cfg["mode"] == "live" else []
            if missing:
                raise SyncError(f"live mode needs {', '.join(missing)} in integrations.env")
            result = await asyncio.wait_for(c.sync(ctx), SYNC_TIMEOUT_S)
        except SyncError as exc:
            error = str(exc)[:500]
        except TimeoutError:
            error = f"no answer within {SYNC_TIMEOUT_S} s"
        except Exception as exc:  # noqa: BLE001 - a connector bug is a failed sync, shown on the dashboard
            self.log.exception("%s sync crashed", c.id)
            error = f"{type(exc).__name__}: {str(exc)[:300]}"

        async with self.db.tx() as conn:
            created = updated = items = 0
            failures = 0 if result is not None else int(cfg["consecutive_failures"]) + 1
            when = next_run(now, failures, int(cfg["interval_s"]))
            if result is not None:
                known = {(r["entity"], r["external_id"]): r["payload_hash"] for r in await (await conn.execute(
                    "SELECT entity, external_id, payload_hash FROM integrations.mappings WHERE connector_id = %s",
                    (c.id,))).fetchall()}
                d = diff(result.records, known)
                for r in d.create:
                    await conn.execute(
                        "INSERT INTO integrations.mappings (connector_id, entity, external_id, internal_id, payload_hash) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (c.id, r.entity, r.external_id, r.internal_id, r.payload_hash))
                for r in d.update:
                    await conn.execute(
                        "UPDATE integrations.mappings SET payload_hash = %s, updated_at = now() WHERE connector_id = %s "
                        "AND entity = %s AND external_id = %s", (r.payload_hash, c.id, r.entity, r.external_id))
                created, updated, items = len(d.create), len(d.update), len(d.create) + len(d.update) + d.same
                await conn.execute(
                    "UPDATE integrations.sync_state SET last_run_at = %s, last_ok_at = %s, consecutive_failures = 0, "
                    "next_run_at = %s, cursor = %s, runs = runs + 1, items = items + %s WHERE connector_id = %s",
                    (now, now, when, Jsonb(result.cursor), created + updated, c.id))
            else:
                await conn.execute(
                    "UPDATE integrations.sync_state SET last_run_at = %s, last_error = %s, last_error_at = %s, "
                    "consecutive_failures = %s, next_run_at = %s, runs = runs + 1 WHERE connector_id = %s",
                    (now, error, now, failures, when, c.id))
            await conn.execute(
                "UPDATE integrations.runs SET finished_at = now(), ok = %s, items = %s, created = %s, updated = %s, "
                "error = %s WHERE id = %s", (result is not None, items, created, updated, error or None, run_id))
            await conn.execute(
                "DELETE FROM integrations.runs WHERE connector_id = %s AND id < (SELECT min(id) FROM (SELECT id FROM "
                "integrations.runs WHERE connector_id = %s ORDER BY id DESC LIMIT %s) k)", (c.id, c.id, RUNS_KEPT))
            done = ConnectorSynced(connector_id=c.id, ok=result is not None, trigger=trigger, items=items, created=created,
                                   updated=updated, error=error, failures=failures,
                                   next_run_s=round((when - now).total_seconds(), 1))
            await self.emit(conn, done, cause=cause)
            if failures == FAILURES_TO_NOTIFY:
                await self.queue(conn, alerts.for_event("integrations.sync.finished", done.model_dump(), date.today()),
                                 cause=cause)
        self.relay.wakeup.set()
        if error:
            self.log.warning("%s sync failed (%d in a row): %s", c.id, failures, error)
        return done

    # ------------------------------------------------------------ owner commands

    @on("cmd.integrations.configure")
    async def configure(self, env, cmd: ConfigureConnector, conn):
        if cmd.connector_id not in self.registry:
            self.log.warning("unknown connector %s", cmd.connector_id)
            return
        sets, params = [], []
        for col, v in (("enabled", cmd.enabled), ("mode", cmd.mode), ("interval_s", cmd.interval_s)):
            if v is not None:
                sets.append(f"{col} = %s")
                params.append(v)
        row = await (await conn.execute(
            f"UPDATE integrations.connectors SET {', '.join([*sets, 'updated_by = %s', 'updated_at = now()'])} "
            "WHERE connector_id = %s RETURNING enabled, mode, interval_s",
            (*params, cmd.changed_by or "unknown", cmd.connector_id))).fetchone()
        if cmd.enabled or cmd.mode or cmd.interval_s:
            # Switched on (or changed): the next tick runs it, and a fresh start forgets earlier failures.
            await conn.execute("UPDATE integrations.sync_state SET next_run_at = now(), consecutive_failures = 0 "
                               "WHERE connector_id = %s", (cmd.connector_id,))
        await self.emit(conn, ConnectorChanged(connector_id=cmd.connector_id, enabled=row["enabled"], mode=row["mode"],
                                               interval_s=row["interval_s"], by=cmd.changed_by or "unknown"), cause=env)

    @on("cmd.integrations.run_now")
    async def run_now(self, env, cmd: RunConnector, conn):
        if cmd.connector_id in self.registry:
            self.after_commit(lambda: self.spawn(self.run(cmd.connector_id, trigger="manual", cause=env)))

    # ------------------------------------------------------------ notifications outbox

    async def queue(self, conn, note: alerts.Note | None, *, cause=None, source: str | None = None) -> int | None:
        if note is None:
            return None
        row = await (await conn.execute(
            "INSERT INTO notify.outbox (topic, severity, title, body, link, dedupe_key, min_role, status, source_event) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'dashboard', %s) "
            "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING RETURNING id",
            (note.topic, note.severity, note.title[:200], note.body[:2000], note.link[:300], note.dedupe_key,
             note.min_role, source))).fetchone()
        if row is None:
            return None                       # already told about this one
        await self.emit(conn, NotificationQueued(notification_id=row["id"], topic=note.topic, severity=note.severity,
                                                 title=note.title[:200], link=note.link[:300]), cause=cause)
        return row["id"]

    async def _from_event(self, env, msg, conn) -> None:
        kind = env.type.removeprefix("io.printfarm.").rsplit(".v", 1)[0]
        await self.queue(conn, alerts.for_event(kind, msg.model_dump(mode="json"), date.today()), cause=env,
                         source=env.id)

    @on("evt.printer.pause.unconfirmed")
    async def pause_unconfirmed(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.printer.failure.detected")
    async def failure_detected(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.printer.failure.suspected")
    async def failure_suspected(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.fleet.maintenance.due")
    async def maintenance_due(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.fleet.plate_stack.empty")
    async def plate_stack_empty(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.fleet.plate.finished")
    async def plate_finished(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.order.order.needs_attention")
    async def needs_attention(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.inventory.alert.raised")
    async def stock_alert(self, env, msg, conn):
        await self._from_event(env, msg, conn)

    @on("evt.notify.requested")
    async def requested(self, env, msg: NotificationRequested, conn):
        await self.queue(conn, alerts.Note(msg.topic, msg.severity, msg.title, msg.body, msg.link, msg.dedupe_key,
                                           msg.min_role), cause=env, source=env.id)

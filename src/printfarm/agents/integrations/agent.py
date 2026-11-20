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
import time
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from printfarm.agents.base import Agent, on
from printfarm.agents.integrations import alerts, couriers, invoicing, stores
from printfarm.agents.integrations.connectors import REGISTRY
from printfarm.agents.integrations.framework import (
    FAILURES_TO_NOTIFY,
    Connector,
    SyncContext,
    SyncError,
    backoff_s,
    clean_settings,
    diff,
    is_due,
    next_run,
    read_env_file,
    secrets_status,
)
from printfarm.contracts.messages import (
    ConfigureConnector,
    ConnectorChanged,
    ConnectorSynced,
    CreateShipment,
    DocumentIssued,
    IssueDocument,
    MapStoreSku,
    NotificationQueued,
    NotificationRequested,
    OrderShipped,
    PaymentRecorded,
    PaymentRefunded,
    RunConnector,
    ShipmentReady,
    ShipmentRefused,
    ShipmentStatusChanged,
    StoreOrderAction,
    StoreOrderChanged,
    StoreOrderReady,
    StorePushFinished,
    StoreSkuMapped,
    UpdateShipment,
)

TICK_S = 5
SYNC_TIMEOUT_S = 120
RUNS_KEPT = 200                  # per connector
PUSH_ATTEMPTS = 8                # then a person is told and it stops trying
PUSH_RETRY_S = 60                # doubled per failed attempt (framework.backoff_s)


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
        await self.push_due(now)
        await self.issue_due(now)
        await self.track_due(now)
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
            missing = c.live_missing(values, c.settings_with_defaults(cfg["settings"] or {})) if cfg["mode"] == "live" else []
            if missing:
                raise SyncError(f"live mode needs {', '.join(missing)} (secrets go in integrations.env)")
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
                if isinstance(c, stores.StoreConnector):
                    await self._store_records(conn, c, [*d.create, *d.update], cfg["settings"] or {}, cause)
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
        if cmd.settings:
            try:
                change = clean_settings(self.registry[cmd.connector_id].settings, cmd.settings)
            except ValueError as exc:
                self.log.warning("%s: settings refused: %s", cmd.connector_id, exc)
                return
            sets.append("settings = settings || %s")
            params.append(Jsonb(change))
        row = await (await conn.execute(
            f"UPDATE integrations.connectors SET {', '.join([*sets, 'updated_by = %s', 'updated_at = now()'])} "
            "WHERE connector_id = %s RETURNING enabled, mode, interval_s",
            (*params, cmd.changed_by or "unknown", cmd.connector_id))).fetchone()
        if cmd.enabled or cmd.mode or cmd.interval_s or cmd.settings:
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

    # ------------------------------------------------------------ stores (W2-A): orders in, tracking back

    async def _catalog(self, conn) -> dict[str, dict]:
        rows = await (await conn.execute(
            "SELECT sku, name, material, color, niche, active FROM catalog.products")).fetchall()
        return {r["sku"]: dict(r) for r in rows}

    async def _maps(self, conn, connector_id: str) -> dict[str, stores.SkuMap]:
        rows = await (await conn.execute(
            "SELECT external_key, product, sku, material, color, text_property FROM integrations.sku_map "
            "WHERE connector_id = %s", (connector_id,))).fetchall()
        return {r["external_key"]: stores.SkuMap(r["product"], r["sku"], r["material"], r["color"], r["text_property"])
                for r in rows}

    async def _store_records(self, conn, c: stores.StoreConnector, records, settings: dict, cause) -> None:
        if not records:
            return
        maps, catalog = await self._maps(conn, c.id), await self._catalog(conn)
        merged = c.settings_with_defaults(settings)
        for r in records:
            await self._store_order(conn, c, stores.StoreOrder.from_payload(r.payload), maps, catalog, merged, cause)

    async def _store_order(self, conn, c: stores.StoreConnector, order: stores.StoreOrder, maps, catalog,
                           settings: dict, cause, *, force: str | None = None) -> str:
        """Decide what one store order is now, import it when it is ready, store it, and tell who must know."""
        prev = await (await conn.execute(
            "SELECT status, order_id, problems FROM integrations.store_orders WHERE connector_id = %s AND external_id = %s",
            (c.id, order.external_id))).fetchone()
        res = stores.resolve(order, maps, catalog)
        since = None
        if settings.get("import_since"):
            since = datetime.fromisoformat(f"{settings['import_since']}T00:00:00+00:00")
        status = force or stores.decide(order, res, previous=prev["status"] if prev else None,
                                        only_paid=bool(settings.get("only_paid", True)), since=since)
        if status == "old":
            return status
        problems = stores.problems_json(res)
        oid = prev["order_id"] if prev and prev["order_id"] else None
        if status == "import":
            oid = stores.order_id(c.order_prefix, order.external_id)
            try:
                ready = StoreOrderReady(**stores.ready(order, res, oid=oid, connector_id=c.id, channel=c.channel))
            except ValidationError as exc:
                status, oid = "needs_mapping", None
                problems = [*problems, {"kind": "problem", "key": "", "title": order.name,
                                        "message": "; ".join(e["msg"] for e in exc.errors())[:300]}]
            else:
                await self.emit(conn, ready, cause=cause, correlationid=oid)
                status = "imported"
        if status == "imported" and order.cancelled:
            problems = [{"kind": "problem", "key": "", "title": order.name,
                         "message": "cancelled at the store after it was imported"}]
            await self.queue(conn, alerts.Note(
                "store", "warning", f"{c.label} order {order.name} was cancelled",
                f"It is order {oid} here. Cancel it here too unless it has shipped.", f"/orders?order={oid}",
                f"store_cancel:{c.id}:{order.external_id}"), cause=cause)
        await conn.execute(
            "INSERT INTO integrations.store_orders (connector_id, external_id, name, customer, total_agorot, currency, "
            "financial_status, placed_at, status, order_id, problems, payload, imported_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'imported' THEN now() END) "
            "ON CONFLICT (connector_id, external_id) DO UPDATE SET name = EXCLUDED.name, customer = EXCLUDED.customer, "
            "total_agorot = EXCLUDED.total_agorot, currency = EXCLUDED.currency, financial_status = EXCLUDED.financial_status, "
            "placed_at = EXCLUDED.placed_at, status = EXCLUDED.status, order_id = coalesce(EXCLUDED.order_id, "
            "integrations.store_orders.order_id), problems = EXCLUDED.problems, payload = EXCLUDED.payload, "
            "imported_at = coalesce(integrations.store_orders.imported_at, EXCLUDED.imported_at), updated_at = now()",
            (c.id, order.external_id, order.name, order.customer_name, order.total_agorot, order.currency,
             order.financial_status, order.placed_at, status, oid, Jsonb(problems), Jsonb(order.to_payload()), status))
        changed = prev is None or prev["status"] != status or (prev["problems"] or []) != problems
        if changed:
            await self.emit(conn, StoreOrderChanged(connector_id=c.id, external_id=order.external_id, name=order.name,
                                                    status=status, previous=prev["status"] if prev else None,
                                                    order_id=oid,
                                                    problems=[p["message"] for p in problems]), cause=cause)
        if status == "needs_mapping" and (prev is None or prev["status"] != "needs_mapping"):
            await self.queue(conn, alerts.Note(
                "store", "warning", f"{c.label} order {order.name} waits for you",
                "; ".join(p["message"] for p in problems)[:500] or "No line of it is one of our products yet.",
                "/store", f"store_map:{c.id}:{order.external_id}"), cause=cause)
        return status

    async def _retry_waiting(self, conn, connector_id: str, cause, external_id: str | None = None,
                             force: str | None = None) -> int:
        """Try again the store orders that wait for a mapping (all of the connector's, or one)."""
        c = self.registry.get(connector_id)
        if not isinstance(c, stores.StoreConnector):
            return 0
        rows = await (await conn.execute(
            "SELECT o.payload, c.settings FROM integrations.store_orders o JOIN integrations.connectors c USING "
            "(connector_id) WHERE o.connector_id = %s AND " + ("o.external_id = %s" if external_id else
                                                               "o.status = 'needs_mapping'") + " FOR UPDATE OF o",
            (connector_id, *((external_id,) if external_id else ())))).fetchall()
        if not rows:
            return 0
        maps, catalog = await self._maps(conn, connector_id), await self._catalog(conn)
        for r in rows:
            await self._store_order(conn, c, stores.StoreOrder.from_payload(r["payload"]), maps, catalog,
                                    c.settings_with_defaults(r["settings"] or {}), cause, force=force)
        return len(rows)

    @on("cmd.integrations.map_sku")
    async def map_sku(self, env, cmd: MapStoreSku, conn):
        if not isinstance(self.registry.get(cmd.connector_id), stores.StoreConnector):
            self.log.warning("map_sku: %s is not a store", cmd.connector_id)
            return
        if cmd.remove:
            await conn.execute("DELETE FROM integrations.sku_map WHERE connector_id = %s AND external_key = %s",
                               (cmd.connector_id, cmd.external_key))
        else:
            await conn.execute(
                "INSERT INTO integrations.sku_map (connector_id, external_key, product, sku, material, color, text_property, "
                "updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (connector_id, external_key) DO UPDATE SET "
                "product = EXCLUDED.product, sku = EXCLUDED.sku, material = EXCLUDED.material, color = EXCLUDED.color, "
                "text_property = EXCLUDED.text_property, updated_by = EXCLUDED.updated_by, updated_at = now()",
                (cmd.connector_id, cmd.external_key, cmd.product, cmd.sku if cmd.product == "catalog" else "",
                 cmd.material, cmd.color, cmd.text_property, cmd.mapped_by or "unknown"))
        await self.emit(conn, StoreSkuMapped(connector_id=cmd.connector_id, external_key=cmd.external_key,
                                             product=cmd.product, sku=cmd.sku, removed=cmd.remove,
                                             by=cmd.mapped_by or "unknown"), cause=env)
        if not cmd.remove:
            await self._retry_waiting(conn, cmd.connector_id, env)

    @on("cmd.integrations.store_order")
    async def store_order_action(self, env, cmd: StoreOrderAction, conn):
        row = await (await conn.execute(
            "SELECT status FROM integrations.store_orders WHERE connector_id = %s AND external_id = %s",
            (cmd.connector_id, cmd.external_id))).fetchone()
        if row is None or row["status"] == "imported":
            self.log.warning("store order %s/%s: nothing to %s", cmd.connector_id, cmd.external_id, cmd.action)
            return
        if cmd.action == "skip":
            await self._retry_waiting(conn, cmd.connector_id, env, cmd.external_id, force="skipped")
            return
        if cmd.action == "unskip":
            await conn.execute("UPDATE integrations.store_orders SET status = 'needs_mapping' WHERE connector_id = %s "
                               "AND external_id = %s AND status = 'skipped'", (cmd.connector_id, cmd.external_id))
        await self._retry_waiting(conn, cmd.connector_id, env, cmd.external_id)

    @on("evt.order.order.shipped")
    async def order_shipped(self, env, msg: OrderShipped, conn):
        """An imported store order shipped: tell the store (fulfilled, with the tracking) on the next tick."""
        row = await (await conn.execute(
            "SELECT connector_id, external_id FROM integrations.store_orders WHERE order_id = %s AND status = 'imported'",
            (msg.order_id,))).fetchone()
        if row is None:
            return
        await conn.execute(
            "INSERT INTO integrations.pushes (connector_id, kind, external_id, order_id, payload) "
            "VALUES (%s, 'tracking', %s, %s, %s) ON CONFLICT (connector_id, kind, external_id) DO NOTHING",
            (row["connector_id"], row["external_id"], msg.order_id,
             Jsonb({"carrier": msg.carrier, "tracking": msg.tracking, "url": msg.tracking_url})))

    async def push_due(self, now: datetime) -> int:
        """Send what is due back to the stores (enabled connectors only), one at a time, retrying with backoff."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT p.id, p.connector_id, p.kind, p.external_id, p.order_id, p.payload, p.attempts, c.mode, c.settings "
                "FROM integrations.pushes p JOIN integrations.connectors c USING (connector_id) "
                "WHERE p.status = 'pending' AND p.next_at <= %s AND c.enabled ORDER BY p.id LIMIT 20", (now,))).fetchall()
        sent = 0
        for r in rows:
            c = self.registry.get(r["connector_id"])
            if not isinstance(c, stores.StoreConnector):
                continue
            values = self.secret_values(c)
            settings = r["settings"] or {}
            ctx = SyncContext(mode=r["mode"], cursor={}, settings=settings, secrets=values, now=now,
                              log=lambda m, cid=c.id: self.log.info("%s: %s", cid, m))
            error = ""
            try:
                missing = c.live_missing(values, c.settings_with_defaults(settings)) if r["mode"] == "live" else []
                if missing:
                    raise SyncError(f"live mode needs {', '.join(missing)}")
                await asyncio.wait_for(c.push(ctx, stores.Push(r["kind"], r["external_id"], r["order_id"], r["payload"])),
                                       SYNC_TIMEOUT_S)
            except SyncError as exc:
                error = str(exc)[:500]
            except TimeoutError:
                error = f"no answer within {SYNC_TIMEOUT_S} s"
            except Exception as exc:  # noqa: BLE001 - a connector bug is a failed push, shown on the dashboard
                self.log.exception("%s push crashed", c.id)
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            attempts = int(r["attempts"]) + 1
            status = "sent" if not error else ("failed" if attempts >= PUSH_ATTEMPTS else "pending")
            async with self.db.tx() as conn:
                await conn.execute(
                    "UPDATE integrations.pushes SET status = %s, attempts = %s, error = %s, next_at = %s, "
                    "sent_at = CASE WHEN %s = 'sent' THEN now() END WHERE id = %s",
                    (status, attempts, error or None,
                     now + timedelta(seconds=backoff_s(attempts, PUSH_RETRY_S)), status, r["id"]))
                await self.emit(conn, StorePushFinished(connector_id=c.id, push_kind=r["kind"], order_id=r["order_id"],
                                                        ok=not error, attempts=attempts, error=error))
                if status == "failed":
                    await self.queue(conn, alerts.Note(
                        "integration", "warning", f"{c.label} did not take the tracking of order {r['order_id']}",
                        f"{PUSH_ATTEMPTS} tries failed: {error}. Mark it fulfilled in {c.label} by hand.",
                        "/store", f"push:{r['id']}", min_role="owner"))
            sent += not error
        if rows:
            self.relay.wakeup.set()
        return sent

    # ------------------------------------------------------------ invoicing (W2-B): a document per payment

    def _invoicer(self, enabled: dict[str, bool]) -> invoicing.InvoicingConnector | None:
        """The invoicing provider in use: the first one switched on."""
        for c in self.registry.values():
            if isinstance(c, invoicing.InvoicingConnector) and enabled.get(c.id):
                return c
        return None

    async def _connectors_state(self, conn) -> dict[str, dict]:
        rows = await (await conn.execute(
            "SELECT connector_id, enabled, mode, settings, interval_s FROM integrations.connectors")).fetchall()
        return {r["connector_id"]: dict(r) for r in rows}

    async def _queue_document(self, conn, *, payment_id: str, order_id: str | None, amount: int, refund: bool,
                              requested_by: str, manual_request: bool = False) -> int | None:
        state = await self._connectors_state(conn)
        c = self._invoicer({k: v["enabled"] for k, v in state.items()})
        if c is None:
            return None
        settings = c.settings_with_defaults(state[c.id]["settings"] or {})
        if not manual_request and not settings.get("auto_issue", True):
            return None
        fs = await (await conn.execute("SELECT business_type FROM finance.settings WHERE id = 1")).fetchone()
        kind = invoicing.kind_for(fs["business_type"] if fs else "murshe", refund)
        row = await (await conn.execute(
            "INSERT INTO integrations.documents (connector_id, payment_id, order_id, kind, amount_agorot, status, error, "
            "requested_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (connector_id, payment_id) DO NOTHING "
            "RETURNING id", (c.id, payment_id, order_id, kind or "credit", amount, "pending" if kind else "manual",
                             None if kind else "osek patur: refund the receipt by hand in the invoicing provider",
                             requested_by))).fetchone()
        if row is not None and kind is None:
            await self.emit(conn, DocumentIssued(payment_id=payment_id, order_id=order_id, connector_id=c.id,
                                                 doc_kind="credit", status="manual",
                                                 error="osek patur: refund the receipt by hand"))
        return row["id"] if row else None

    @on("evt.order.payment.recorded")
    async def payment_recorded(self, env, msg: PaymentRecorded, conn):
        await self._queue_document(conn, payment_id=msg.payment_id, order_id=msg.order_id, amount=msg.amount_agorot,
                                   refund=False, requested_by="auto")

    @on("evt.order.payment.refunded")
    async def payment_refunded(self, env, msg: PaymentRefunded, conn):
        await self._queue_document(conn, payment_id=msg.payment_id, order_id=msg.order_id, amount=msg.amount_agorot,
                                   refund=True, requested_by="auto")

    @on("cmd.integrations.issue_document")
    async def issue_document(self, env, cmd: IssueDocument, conn):
        """A person asks for a payment's document: queue it, or try a failed one again."""
        cur = await conn.execute(
            "UPDATE integrations.documents SET status = 'pending', attempts = 0, next_at = now(), error = NULL, "
            "requested_by = %s WHERE payment_id = %s AND status IN ('failed', 'pending')",
            (cmd.requested_by or "unknown", cmd.payment_id))
        if cur.rowcount:
            return
        p = await (await conn.execute(
            "SELECT payment_id, order_id, kind, amount_agorot FROM coordinator.payments WHERE payment_id = %s",
            (cmd.payment_id,))).fetchone()
        if p is None:
            self.log.warning("issue_document: no payment %s", cmd.payment_id)
            return
        await self._queue_document(conn, payment_id=p["payment_id"], order_id=p["order_id"], amount=p["amount_agorot"],
                                   refund=p["kind"] == "refund", requested_by=cmd.requested_by or "unknown",
                                   manual_request=True)

    async def _doc_request(self, conn, d: dict) -> invoicing.DocRequest:
        p = await (await conn.execute(
            "SELECT payment_id, amount_agorot, method, at FROM coordinator.payments WHERE payment_id = %s",
            (d["payment_id"],))).fetchone()
        if p is None:
            raise SyncError(f"payment {d['payment_id']} is not recorded")
        order, items = None, []
        if d["order_id"]:
            order = await (await conn.execute(
                "SELECT order_id, customer_name, customer_ref, total_agorot, shipping_agorot, discount_agorot "
                "FROM coordinator.orders WHERE order_id = %s", (d["order_id"],))).fetchone()
            items = await (await conn.execute(
                "SELECT i.product, i.text, i.quantity, i.line_total_agorot, coalesce(c.name, i.product) AS label "
                "FROM coordinator.order_items i LEFT JOIN catalog.products c ON c.sku = i.sku AND i.product = 'catalog' "
                "WHERE i.order_id = %s ORDER BY i.item_id::int", (d["order_id"],))).fetchall()
            items = [{**i, "label": "Nameplate" if i["product"] == "nameplate" else i["label"]} for i in items]
        linked = None
        if d["kind"] == "credit" and d["order_id"]:
            row = await (await conn.execute(
                "SELECT external_id FROM integrations.documents WHERE order_id = %s AND kind <> 'credit' "
                "AND status = 'issued' ORDER BY issued_at DESC LIMIT 1", (d["order_id"],))).fetchone()
            linked = row["external_id"] if row else None
        fs = await (await conn.execute("SELECT business_type FROM finance.settings WHERE id = 1")).fetchone()
        return invoicing.build(kind=d["kind"], payment=dict(p), order=dict(order) if order else None,
                               items=[dict(i) for i in items], vat_exempt=(fs or {}).get("business_type") == "patur",
                               linked_external_id=linked)

    async def issue_due(self, now: datetime) -> int:
        """Issue the documents that are due with their provider (enabled only), retrying with backoff."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT d.*, c.mode, c.settings FROM integrations.documents d JOIN integrations.connectors c "
                "USING (connector_id) WHERE d.status = 'pending' AND d.next_at <= %s AND c.enabled ORDER BY d.id LIMIT 20",
                (now,))).fetchall()
        done = 0
        for d in rows:
            c = self.registry.get(d["connector_id"])
            if not isinstance(c, invoicing.InvoicingConnector):
                continue
            values = self.secret_values(c)
            ctx = SyncContext(mode=d["mode"], cursor={}, settings=d["settings"] or {}, secrets=values, now=now,
                              log=lambda m, cid=c.id: self.log.info("%s: %s", cid, m))
            error, issued, req = "", None, None
            try:
                missing = c.live_missing(values, c.settings_with_defaults(d["settings"] or {})) if d["mode"] == "live" else []
                if missing:
                    raise SyncError(f"live mode needs {', '.join(missing)}")
                async with self.db.tx() as conn:
                    req = await self._doc_request(conn, d)
                issued = await asyncio.wait_for(c.issue(ctx, req, d["id"]), SYNC_TIMEOUT_S)
            except SyncError as exc:
                error = str(exc)[:500]
            except TimeoutError:
                error = f"no answer within {SYNC_TIMEOUT_S} s"
            except Exception as exc:  # noqa: BLE001 - a connector bug is a failed document, shown on the dashboard
                self.log.exception("%s issue crashed", c.id)
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            attempts = int(d["attempts"]) + 1
            status = "issued" if issued else ("failed" if attempts >= PUSH_ATTEMPTS else "pending")
            async with self.db.tx() as conn:
                await conn.execute(
                    "UPDATE integrations.documents SET status = %s, attempts = %s, error = %s, next_at = %s, "
                    "external_id = %s, number = %s, url = %s, allocation = %s, request = %s, "
                    "issued_at = CASE WHEN %s = 'issued' THEN now() END WHERE id = %s",
                    (status, attempts, error or None, now + timedelta(seconds=backoff_s(attempts, PUSH_RETRY_S)),
                     issued.external_id if issued else None, issued.number if issued else None,
                     issued.url if issued else None, (issued.allocation or None) if issued else None,
                     Jsonb(req.to_json() if req else {}), status, d["id"]))
                await self.emit(conn, DocumentIssued(payment_id=d["payment_id"], order_id=d["order_id"], connector_id=c.id,
                                                     doc_kind=d["kind"], status=status, number=issued.number if issued else "",
                                                     url=issued.url if issued else "", error=error))
                if status == "failed":
                    await self.queue(conn, alerts.Note(
                        "integration", "warning", f"{c.label} did not issue the document of payment {d['payment_id']}",
                        f"{PUSH_ATTEMPTS} tries failed: {error}. Issue it again from Finance > Documents, or in {c.label}.",
                        "/finance?tab=documents", f"doc:{d['id']}", min_role="owner"))
            done += bool(issued)
        if rows:
            self.relay.wakeup.set()
        return done

    # ------------------------------------------------------------ shipping (W2-B): parcels and where they are

    async def _refuse_shipment(self, conn, env, cmd: CreateShipment, reason: str) -> None:
        self.log.info("shipment of %s refused: %s", cmd.order_id, reason)
        await self.emit(conn, ShipmentRefused(order_id=cmd.order_id, carrier=cmd.carrier, reason=reason,
                                              by=cmd.created_by or "unknown"), cause=env, correlationid=cmd.order_id)

    @on("cmd.integrations.create_shipment")
    async def create_shipment(self, env, cmd: CreateShipment, conn):
        saga = await (await conn.execute(
            "SELECT state, context FROM coordinator.sagas WHERE saga_id = %s", (cmd.order_id,))).fetchone()
        if saga is None:
            return await self._refuse_shipment(conn, env, cmd, f"no order {cmd.order_id}")
        passed = saga["state"] == "QC" and (saga["context"] or {}).get("qc", {}).get("status") == "passed"
        if not passed and saga["state"] != "SHIPPED":
            return await self._refuse_shipment(conn, env, cmd, "only an order that passed QC can be shipped")
        live = await (await conn.execute(
            "SELECT shipment_id FROM integrations.shipments WHERE order_id = %s AND status <> 'cancelled'",
            (cmd.order_id,))).fetchone()
        if live is not None:
            return await self._refuse_shipment(conn, env, cmd, f"it already has parcel {live['shipment_id']}: cancel "
                                                               "that label first")
        n = (await (await conn.execute("SELECT count(*) AS n FROM integrations.shipments WHERE order_id = %s",
                                       (cmd.order_id,))).fetchone())["n"] + 1
        sid = couriers.shipment_id(cmd.order_id, n)
        courier = self.registry.get(cmd.carrier)
        tracking, tracking_url, label_url, connector_id, carrier = cmd.tracking.strip(), cmd.tracking_url, "", None, cmd.carrier
        service = cmd.service
        if isinstance(courier, couriers.CourierConnector):
            state = (await self._connectors_state(conn)).get(courier.id, {})
            if not state.get("enabled"):
                return await self._refuse_shipment(conn, env, cmd, f"{courier.label} is switched off (Integrations)")
            settings = courier.settings_with_defaults(state.get("settings") or {})
            service = service or str(settings.get("service") or "")
            ctx = SyncContext(mode=state["mode"], cursor={}, settings=state.get("settings") or {},
                              secrets=self.secret_values(courier), now=datetime.now(UTC),
                              log=lambda m: self.log.info("%s: %s", courier.id, m))
            try:
                label = await asyncio.wait_for(courier.create_label(ctx, cmd.order_id, cmd.ship_to.model_dump(),
                                                                    cmd.weight_g, service, int(time.time()) % 1_000_000),
                                               SYNC_TIMEOUT_S)
            except (SyncError, TimeoutError) as exc:
                return await self._refuse_shipment(conn, env, cmd, f"{courier.label}: {exc or 'no answer'}")
            if label is not None:
                tracking, label_url = label.tracking, label.label_url
                tracking_url = label.tracking_url or courier.tracking_url(tracking)
            elif not tracking:
                return await self._refuse_shipment(conn, env, cmd, f"make the label at {courier.label} and type its "
                                                                   "tracking number")
            elif not courier.valid_tracking(tracking):
                return await self._refuse_shipment(conn, env, cmd, f"{tracking} is not a {courier.label} tracking number")
            else:
                tracking = tracking.upper()
                tracking_url = courier.tracking_url(tracking)
            connector_id, carrier = courier.id, courier.label
        label_url = label_url or f"/api/v1/shipments/{sid}/label"
        by = cmd.created_by or "unknown"
        await conn.execute(
            "INSERT INTO integrations.shipments (shipment_id, order_id, connector_id, carrier, service, tracking, "
            "tracking_url, label_url, ship_to, weight_g, events, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, cmd.order_id, connector_id, carrier, service, tracking, tracking_url, label_url,
             Jsonb(cmd.ship_to.model_dump()), cmd.weight_g,
             Jsonb([{"status": "label_created", "at": datetime.now(UTC).isoformat(), "text": "", "by": by}]), by))
        await self.emit(conn, ShipmentReady(shipment_id=sid, order_id=cmd.order_id, carrier=carrier, tracking=tracking,
                                            tracking_url=tracking_url, label_url=label_url, by=by,
                                            mark_shipped=cmd.mark_shipped and passed), cause=env, correlationid=cmd.order_id)

    async def _move_shipment(self, conn, sid: str, to: str, *, text: str, by: str, cause=None) -> str | None:
        row = await (await conn.execute(
            "SELECT order_id, status, events FROM integrations.shipments WHERE shipment_id = %s FOR UPDATE",
            (sid,))).fetchone()
        if row is None:
            return f"no parcel {sid}"
        why = couriers.can_move(row["status"], to)
        if why:
            return why
        events = [*(row["events"] or []), {"status": to, "at": datetime.now(UTC).isoformat(), "text": text, "by": by}]
        await conn.execute(
            "UPDATE integrations.shipments SET status = %s, events = %s, status_at = now(), updated_at = now() "
            "WHERE shipment_id = %s", (to, Jsonb(events), sid))
        await self.emit(conn, ShipmentStatusChanged(shipment_id=sid, order_id=row["order_id"], status=to,
                                                    previous=row["status"], text=text, by=by), cause=cause,
                        correlationid=row["order_id"])
        return None

    @on("cmd.integrations.update_shipment")
    async def update_shipment(self, env, cmd: UpdateShipment, conn):
        why = await self._move_shipment(conn, cmd.shipment_id, cmd.status, text=cmd.note, by=cmd.changed_by or "unknown",
                                        cause=env)
        if why:
            self.log.warning("parcel %s not moved to %s: %s", cmd.shipment_id, cmd.status, why)

    async def track_due(self, now: datetime) -> int:
        """Ask the couriers where their open parcels are (each at most once per the connector's interval)."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT s.shipment_id, s.connector_id, s.tracking, s.status, s.events, c.mode, c.settings "
                "FROM integrations.shipments s JOIN integrations.connectors c USING (connector_id) "
                "WHERE s.status IN ('label_created', 'in_transit') AND c.enabled AND s.tracking <> '' "
                "AND (s.last_checked_at IS NULL OR s.last_checked_at <= %s - make_interval(secs => c.interval_s)) "
                "ORDER BY s.last_checked_at NULLS FIRST LIMIT 20", (now,))).fetchall()
        moved = 0
        for r in rows:
            c = self.registry.get(r["connector_id"])
            if not isinstance(c, couriers.CourierConnector):
                continue
            ctx = SyncContext(mode=r["mode"], cursor={}, settings=r["settings"] or {}, secrets=self.secret_values(c),
                              now=now, log=lambda m, cid=c.id: self.log.info("%s: %s", cid, m))
            checks = sum(1 for e in r["events"] or [] if e.get("by") == "courier")
            try:
                found = await asyncio.wait_for(c.track(ctx, r["tracking"], r["status"], checks), SYNC_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - tracking is best effort: try again next time
                self.log.warning("%s: tracking %s failed: %s", c.id, r["tracking"], exc)
                found = []
            async with self.db.tx() as conn:
                await conn.execute("UPDATE integrations.shipments SET last_checked_at = %s WHERE shipment_id = %s",
                                   (now, r["shipment_id"]))
                step = couriers.latest(found, r["status"])
                if step is not None and await self._move_shipment(conn, r["shipment_id"], step.status, text=step.text,
                                                                  by="courier") is None:
                    moved += 1
        if moved:
            self.relay.wakeup.set()
        return moved

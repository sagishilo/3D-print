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
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from printfarm.agents.base import Agent, on
from printfarm.agents.integrations import alerts, stores
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
    MapStoreSku,
    NotificationQueued,
    NotificationRequested,
    OrderShipped,
    RunConnector,
    StoreOrderAction,
    StoreOrderChanged,
    StoreOrderReady,
    StorePushFinished,
    StoreSkuMapped,
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
             Jsonb({"carrier": msg.carrier, "tracking": msg.tracking, "url": getattr(msg, "tracking_url", "") or ""})))

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

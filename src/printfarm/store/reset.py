"""A clean start before real printers: remove demo and test data, keep the setup.

    printfarm reset-data                 # dry run: what would be removed, nothing changes
    printfarm reset-data --yes           # do it (stop the agents first; README "Clean start")

Removed: orders and everything that hangs off them (payments, QC, sagas, finished goods), print jobs and plates,
printer rows and counters (the fleet agent writes them again from fleet.toml on start, with initial_print_hours),
spools, purchase orders, inventory alerts and ledgers, income and expense entries, maintenance tasks and log, store
orders, documents and parcels, notifications, social captures and leads, reports and trends, telemetry history,
the message inbox / outbox, capture tokens, the audit log, test users, and the event history on NATS (so no agent
can replay old orders into the empty tables). With the catalog (default; --keep-catalog to keep it): products,
their tags, bills of materials, parts and packaging items, and every niche except the five built-in ones.

Kept: the users you name (default: admin and e2e-owner), every setting (NATS "config" bucket: automation, alerts,
quiet hours), the finance settings (VAT, tariffs, labour, channel fees: only their confirmation is cleared, so the
owner confirms them once more for the real business), filament materials and reorder rules, filament prices,
connector settings (integrations.connectors), settings history, uploads and generated files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEED_NICHES = ("home", "kids", "mechanical", "gifts", "unassigned")
KEEP_USERS = ("admin", "e2e-owner")

# Emptied whole; one TRUNCATE (no CASCADE): a kept table that still pointed at one of these would make it fail
# instead of silently emptying more than this list.
DATA_TABLES = (
    "coordinator.approvals", "coordinator.fg_ledger", "coordinator.finished_goods", "coordinator.order_items",
    "coordinator.orders", "coordinator.payments", "coordinator.qc_checks", "coordinator.saga_history",
    "coordinator.sagas",
    "finance.income_lines", "finance.income_entries", "finance.expense_entries",
    "fleet.plate_items", "fleet.jobs", "fleet.plates", "fleet.maintenance_log", "fleet.maintenance_tasks",
    "fleet.printers",
    "inventory.alerts", "inventory.ledger", "inventory.spools", "inventory.purchase_orders", "inventory.reservations",
    "personalization.artifacts",
    "social.leads", "social.captures", "social.llm_calls", "social.examples",
    "reporting.daily_niche", "reporting.daily_sku", "reporting.rollups", "trend.insights", "trend.signals",
    "integrations.runs", "integrations.mappings", "integrations.sync_state", "integrations.store_orders",
    "integrations.pushes", "integrations.documents", "integrations.shipments", "integrations.sku_map",
    "notify.outbox",
    "telemetry.calibrations", "telemetry.failures", "telemetry.pause_checks", "telemetry.plate_starts",
    "telemetry.print_sessions",
    "messaging.inbox", "messaging.outbox",
    "gateway.capture_tokens", "gateway.audit_log",
)
CATALOG_TABLES = ("catalog.product_tags", "catalog.product_images", "catalog.products", "inventory.bom", "inventory.item_ledger",
                  "inventory.items", "social.sku_vectors")

# NATS: the event and command history and the live printer state. Not the "config" bucket (settings) nor uploads.
STREAMS = ("EVENTS", "COMMANDS", "TELEMETRY", "DLQ", "KV_printer_status", "KV_heartbeats", "KV_leases")


@dataclass
class Plan:
    tables: list[str]
    keep_users: tuple[str, ...]
    keep_catalog: bool
    streams: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)
    users_removed: list[str] = field(default_factory=list)
    niches_removed: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"  {t:<34} {self.counts.get(t, 0):>7} rows" for t in self.tables]
        out.append(f"  users removed ({len(self.users_removed)}): {', '.join(self.users_removed) or '-'}")
        out.append(f"  users kept: {', '.join(self.keep_users)}")
        if not self.keep_catalog:
            out.append(f"  niches removed ({len(self.niches_removed)}): {', '.join(self.niches_removed) or '-'}")
        out.append("  finance settings: kept; confirmation cleared (confirm them again in Finance > Settings)")
        out.append(f"  NATS history purged: {', '.join(self.streams) or '-'}")
        return out


def plan(keep_users: tuple[str, ...] = KEEP_USERS, keep_catalog: bool = False, purge_streams: bool = True) -> Plan:
    tables = list(DATA_TABLES) + ([] if keep_catalog else list(CATALOG_TABLES))
    return Plan(tables, tuple(dict.fromkeys(u.strip() for u in keep_users if u.strip())), keep_catalog,
                STREAMS if purge_streams else ())


async def survey(conn, p: Plan) -> Plan:
    for t in p.tables:
        p.counts[t] = (await (await conn.execute(f"SELECT count(*) AS n FROM {t}")).fetchone())["n"]  # noqa: S608
    users = await (await conn.execute("SELECT username, role, disabled FROM gateway.users ORDER BY username")).fetchall()
    kept_owners = [u for u in users if u["username"] in p.keep_users and u["role"] == "owner" and not u["disabled"]]
    if not kept_owners:
        raise SystemExit("refusing: none of the users kept (" + ", ".join(p.keep_users) + ") is an active owner; "
                         "name your owner account with --keep-user")
    p.users_removed = [u["username"] for u in users if u["username"] not in p.keep_users]
    if not p.keep_catalog:
        rows = await (await conn.execute("SELECT slug FROM catalog.niches WHERE NOT (slug = ANY(%s)) ORDER BY slug",
                                         (list(SEED_NICHES),))).fetchall()
        p.niches_removed = [r["slug"] for r in rows]
    return p


async def apply(conn, p: Plan, by: str = "reset-data") -> None:
    """Remove the data of the plan in one transaction (``conn`` is inside db.tx())."""
    await conn.execute(f"TRUNCATE {', '.join(p.tables)} RESTART IDENTITY")
    await conn.execute("DELETE FROM gateway.refresh_tokens WHERE user_id IN "
                       "(SELECT id FROM gateway.users WHERE NOT (username = ANY(%s)))", (list(p.keep_users),))
    await conn.execute("DELETE FROM gateway.users WHERE NOT (username = ANY(%s))", (list(p.keep_users),))
    if not p.keep_catalog:
        await conn.execute("DELETE FROM catalog.niches WHERE NOT (slug = ANY(%s))", (list(SEED_NICHES),))
    await conn.execute("UPDATE finance.settings SET confirmed_by = NULL, confirmed_at = NULL, updated_by = %s, "
                       "updated_at = now()", (by,))
    await conn.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES "
        "('reset-' || md5(now()::text), %s, 'system.reset_data', %s::jsonb, 'accepted')",
        (by, '{"note": "demo and test data removed before real printers (printfarm reset-data)"}'))


async def purge(js, streams: tuple[str, ...]) -> list[str]:
    """Purge the NATS streams that exist; returns those purged."""
    done = []
    for name in streams:
        try:
            await js.purge_stream(name)
        except Exception as exc:  # noqa: BLE001 - a stream that does not exist (yet) has nothing to purge
            if "not found" not in str(exc).lower():
                raise
            continue
        done.append(name)
    return done

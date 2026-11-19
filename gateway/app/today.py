"""Today (Wave 1 / M7): everything across the farm that waits for a person, in one ranked list.

GET /api/v1/today?niche= returns items grouped Now / Today / This week. Each item says what, why, and offers one
action: a command (sent through the confirm dialog) or a link. The builders below are pure (rows in, items out)
and unit-tested; the endpoint only reads. A source that fails (NATS briefly down, a table missing) is skipped, so
Today always opens. Items carry the role their action needs and whether they are technical (Expert mode only).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request

from printfarm.contracts.stock import needs_drying, restock_suggestion

from . import auth, system
from .config import settings
from .niches import niche_scope

router = APIRouter(dependencies=[Depends(auth.require_auth)])
PRIORITY = {"now": 300, "today": 200, "week": 100}
STUCK_AFTER = timedelta(hours=2)
RECEIVABLE_DAYS = 14


@dataclass
class Item:
    id: str
    kind: str
    priority: str                 # now | today | week
    title: str
    why: str
    action: dict                  # {"type": "command", "name", "body", "label", "confirm"} | {"type": "link", "to", "label"}
    min_role: str = "operator"    # who may act (viewers see the item, not the button)
    visible_to: str = "viewer"    # who sees the item at all (money: operators)
    expert: bool = False          # technical: Expert mode only
    niche: str | None = None
    age_s: float = 0.0
    count: int = 1
    meta: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        return PRIORITY[self.priority] + min(self.age_s / 3600, 72)      # older first within a priority


def command(name: str, body: dict, label: str, confirm: str) -> dict:
    return {"type": "command", "name": name, "body": body, "label": label, "confirm": confirm}


def link(to: str, label: str) -> dict:
    return {"type": "link", "to": to, "label": label}


def _age(at, now: datetime) -> float:
    if not at:
        return 0.0
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    return max(0.0, (now - at).total_seconds())


# ---------------------------------------------------------------- builders (pure)


def beds(printers: list[dict]) -> list[Item]:
    """A printer that finished and waits for its bed to be cleared cannot start the next plate."""
    return [Item(id=f"bed:{p['printer_id']}", kind="bed", priority="now", title=f"Clear the bed of {p['printer_id']}",
                 why="The print is done; the next plate waits until someone takes it off and confirms.",
                 action=command("printers.clear_bed", {"printer_id": p["printer_id"]}, "Bed cleared",
                                f"Is the bed of {p['printer_id']} empty and clean?"))
            for p in printers if p.get("online") and not p.get("busy") and not p.get("bed_clear")]


def orders(cards: list[dict], now: datetime) -> list[Item]:
    out: list[Item] = []
    for o in cards:
        oid, state, who = o["order_id"], o["state"], o.get("customer_name") or ""
        since, niche = _age(o.get("stage_since"), now), o.get("niche")
        if state == "NEEDS_ATTENTION":
            out.append(Item(id=f"attention:{oid}", kind="attention", priority="now", niche=niche, age_s=since,
                            title=f"Order {oid} needs attention", why=o.get("status") or "a step failed",
                            action=link(f"/orders?order={oid}", "Open")))
        elif state == "LEAD":
            out.append(Item(id=f"lead:{oid}", kind="confirm", priority="today", niche=niche, age_s=since,
                            title=f"Confirm the order of {who}", why=f"{oid} is a lead: nothing starts until it is confirmed.",
                            action=command("orders.advance", {"order_id": oid, "to_state": "RECEIVED"}, "Confirm",
                                           f"Confirm order {oid} of {who}?")))
        elif state == "QC" and o.get("qc") != "passed":
            out.append(Item(id=f"qc:{oid}", kind="qc", priority="today", niche=niche, age_s=since,
                            title=f"Check the quality of {oid}", why=f"{who}: every unit is printed and waits for its check.",
                            action=link(f"/orders?order={oid}", "Check")))
        elif state == "QC":
            out.append(Item(id=f"ship:{oid}", kind="ship", priority="today", niche=niche, age_s=since,
                            title=f"Ship {oid} to {who}", why="It passed quality control.",
                            action=link(f"/orders?order={oid}", "Ship")))
        elif o.get("stuck") and state in ("RECEIVED", "PERSONALIZING", "SCHEDULING"):
            out.append(Item(id=f"stuck:{oid}", kind="stuck", priority="today", niche=niche, age_s=since,
                            title=f"Order {oid} is waiting in {state.title()}",
                            why=f"{o.get('status') or 'no progress'} · for more than {int(STUCK_AFTER.total_seconds() // 3600)} hours",
                            action=link(f"/orders?order={oid}", "Open")))
    return out


def leads(rows: list[dict], now: datetime) -> list[Item]:
    if not rows:
        return []
    oldest = max(_age(r.get("created_at"), now) for r in rows)
    n = len(rows)
    return [Item(id="leads", kind="leads", priority="today", count=n, age_s=oldest,
                 title=f"{n} social repl{'ies' if n != 1 else 'y'} to review or post",
                 why="People asked for something you print; an answer the same day sells best.",
                 action=link("/social", "Review"))]


def approvals(rows: list[dict], now: datetime) -> list[Item]:
    return [Item(id=f"approval:{r['approval_id']}", kind="approval", priority="today", age_s=_age(r.get("created_at"), now),
                 title=r["summary"], why=f"Waiting for your approval ({r['action']}, asked by {r['requested_by']}).",
                 action={"type": "approvals", "label": "Open approvals"}) for r in rows]


def purchase_orders(rows: list[dict], now: datetime) -> list[Item]:
    out = []
    for p in rows:
        what = (f"{float(p.get('quantity') or 0):g} × {p['material']}" if p.get("item_id")
                else f"{p['spools']} × {p['material']} {p['color']}")
        if p["status"] == "placed":
            out.append(Item(id=f"po:{p['po_id']}", kind="po", priority="week", age_s=_age(p.get("created_at"), now),
                            title=f"Order {what} from the supplier", why="The purchase is decided: order it, then confirm the total.",
                            action=link("/filament", "Open")))
        elif p["status"] == "ordered":
            out.append(Item(id=f"po:{p['po_id']}", kind="receive", priority="week", age_s=_age(p.get("ordered_at"), now),
                            title=f"Receive {what} when it arrives", why="Ordered: receive it so the stock counts it.",
                            action=link("/filament", "Open")))
    return out


def stock_alerts(alerts: list[dict], now: datetime) -> list[Item]:
    out = []
    for a in alerts:
        pr = "now" if a["kind"] == "spool_short" else "today" if a["kind"] == "spool_empty" else "week"
        out.append(Item(id=f"alert:{a['alert_id']}", kind=a["kind"], priority=pr, age_s=_age(a.get("opened_at"), now),
                        title=a["message"].split(":")[0] if a["kind"] == "low_item" else a["message"],
                        why=a["message"] if a["kind"] == "low_item" else
                        {"spool_short": "A scheduled plate would run out of filament.", "spool_empty": "Replace the spool.",
                         "low_stock": "Below the minimum: reorder."}.get(a["kind"], ""),
                        action=link("/filament?tab=items" if a["kind"] == "low_item" else "/filament", "Open")))
    return out


def shelf(rows: list[dict]) -> list[Item]:
    out = []
    for r in rows:
        need = restock_suggestion(r["on_hand"], r["reserved"], r["min_stock"], r["in_production"])
        if need:
            out.append(Item(id=f"shelf:{r['sku']}", kind="restock", priority="week", niche=r.get("niche"),
                            title=f"Restock {r['sku']} ({r['on_hand'] - r['reserved']} free, minimum {r['min_stock']})",
                            why=f"{r['name']}: below its shelf minimum.",
                            action=command("catalog.batch", {"items": [{"product": "catalog", "sku": r["sku"], "quantity": need}],
                                                              "notes": f"restock {r['sku']} (Today)"},
                                           f"Queue {need}", f"Queue a stock batch of {need} × {r['sku']}?")))
    return out


def drying(spools: list[dict], now: datetime) -> list[Item]:
    due = [s for s in spools if needs_drying(s["material"], s.get("opened_at"), s.get("dried_at"), now)]
    return [Item(id=f"dry:{s['spool_id']}", kind="dry", priority="week",
                 title=f"Dry spool {s['spool_id']} ({s['material']} {s['color']})",
                 why="Open long enough to take on moisture: dry it, then mark it dried.",
                 action=command("spools.dried", {"spool_id": s["spool_id"]}, "Mark dried", f"Spool {s['spool_id']} is dried?"))
            for s in due]


def receivables(rows: list[dict], now: datetime) -> list[Item]:
    late = [r for r in rows if _age(r.get("shipped_at"), now) > RECEIVABLE_DAYS * 86400]
    if not late:
        return []
    total = sum(int(r["due_agorot"]) for r in late)
    return [Item(id="receivables", kind="receivable", priority="week", count=len(late), visible_to="operator",
                 age_s=max(_age(r.get("shipped_at"), now) for r in late),
                 title=f"{len(late)} order{'s' if len(late) != 1 else ''} unpaid for over {RECEIVABLE_DAYS} days "
                       f"(₪{total / 100:,.0f})", why="Shipped but not paid: send a friendly reminder.",
                 action=link("/finance?tab=receivables", "Open"))]


def margins(below: int) -> list[Item]:
    if not below:
        return []
    return [Item(id="margins", kind="margin", priority="week", count=below, visible_to="operator",
                 title=f"{below} product{'s' if below != 1 else ''} below the niche's target margin",
                 why="Their price does not cover the target after real costs: raise it, or make them cheaper to print.",
                 action=link("/finance?tab=products", "Open"))]


def backup(health: dict) -> list[Item]:
    if health.get("state") != "problem":
        return []
    return [Item(id="backup", kind="backup", priority="today", visible_to="operator", min_role="owner",
                 title="Backups need attention", why=" ".join(health.get("problems") or [])[:300],
                 action=link("/system", "Open"))]


def technical(dlq_total: int, agents: list[dict]) -> list[Item]:
    out = []
    if dlq_total:
        out.append(Item(id="dlq", kind="dlq", priority="today", expert=True, count=dlq_total, visible_to="operator",
                        title=f"{dlq_total} failed message{'s' if dlq_total != 1 else ''}",
                        why="An agent could not handle them: replay or discard.", action=link("/system?tab=dlq", "Open")))
    down = [a["label"] for a in agents if a["status"] in ("down", "late") and a.get("tier") == "core"]
    if down:
        out.append(Item(id="agents", kind="agents", priority="now", expert=True, count=len(down), visible_to="operator",
                        title=f"{', '.join(down)} not running", why="Their work waits until they are back.",
                        action=link("/system?tab=agents", "Open")))
    return out


def rank(items: list[Item], *, role: str | None, niche: str | None = None) -> list[Item]:
    """What this person sees: items they may see, of the chosen niche (farm-wide items always), most urgent first."""
    keep = [i for i in items if auth.allows(role, i.visible_to) and (niche is None or i.niche in (None, niche))]
    return sorted(keep, key=lambda i: (-i.score, i.id))


# ---------------------------------------------------------------- the endpoint


async def _safe(fn, default):
    try:
        return await fn()
    except Exception:  # noqa: BLE001 - one source down must not hide the rest of Today
        return default


@router.get("/api/v1/today")
async def today(request: Request, niche: str | None = None, claims: dict = Depends(auth.require_auth)):
    db, bus, now = request.app.state.db, request.app.state.bus, datetime.now(UTC)
    scope = niche_scope(niche)
    items: list[Item] = []

    printers = await _safe(lambda: db.fetchall("SELECT printer_id, online, busy, bed_clear FROM fleet.printers"), [])
    items += beds(printers)

    from . import orders as orders_mod  # the board's cards: one place decides "stuck" and the status line
    cards = await _safe(lambda: orders_mod._cards(
        db, "s.state IN ('LEAD', 'RECEIVED', 'PERSONALIZING', 'SCHEDULING', 'QC', 'NEEDS_ATTENTION') AND o.channel <> 'stock'",
        (), 300), [])
    for c in cards:
        niches = sorted({i.get("niche") for i in c.get("items", []) if i.get("niche")})
        c["niche"] = niches[0] if len(niches) == 1 else None
        c["niches"] = niches
    if scope:
        cards = [c for c in cards if scope in c["niches"]]
    items += orders(cards, now)

    items += leads(await _safe(lambda: db.fetchall(
        "SELECT lead_id, created_at FROM social.leads WHERE status IN ('pending', 'approved')"), []), now)
    items += approvals(await _safe(lambda: db.fetchall(
        "SELECT approval_id, action, requested_by, summary, created_at FROM coordinator.approvals "
        "WHERE status = 'pending' ORDER BY created_at"), []), now)
    items += purchase_orders(await _safe(lambda: db.fetchall(
        "SELECT p.po_id, coalesce(i.name, p.material) AS material, p.color, p.spools, p.status, p.item_id, "
        "p.quantity::float AS quantity, p.created_at, p.ordered_at FROM inventory.purchase_orders p "
        "LEFT JOIN inventory.items i ON i.item_id = p.item_id WHERE p.status IN ('placed', 'ordered')"), []), now)
    items += stock_alerts(await _safe(lambda: db.fetchall(
        "SELECT alert_id, kind, message, opened_at FROM inventory.alerts WHERE resolved_at IS NULL"), []), now)
    items += shelf(await _safe(lambda: db.fetchall(
        "SELECT f.sku, p.name, p.niche, f.on_hand, f.reserved, f.min_stock, "
        "coalesce((SELECT sum(i.quantity) FROM coordinator.order_items i JOIN coordinator.orders o ON o.order_id = i.order_id "
        "JOIN coordinator.sagas s ON s.saga_id = i.order_id WHERE i.sku = f.sku AND o.channel = 'stock' "
        "AND s.state NOT IN ('SHIPPED', 'CANCELLED')), 0) AS in_production "
        "FROM coordinator.finished_goods f JOIN catalog.products p ON p.sku = f.sku WHERE f.min_stock > 0"), []))
    items += drying(await _safe(lambda: db.fetchall(
        "SELECT spool_id, material, color, opened_at, dried_at FROM inventory.spools WHERE status <> 'empty' "
        "AND opened_at IS NOT NULL"), []), now)

    if auth.allows(claims.get("role"), "operator"):
        from . import analytics, finance, pricing
        items += receivables(await _safe(lambda: finance._receivables(db), []), now)
        below = await _safe(lambda: _below(db, analytics, pricing), 0)
        items += margins(below)
        try:
            with open(settings.backup_status_file, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = None
        if st is not None:                        # no status file at all (tests, a fresh install): nothing to nag
            items += backup(system.backup_health(st, time.time()))
        dlq_total = await _safe(lambda: _dlq_total(bus), 0)
        agents = (await _safe(lambda: system.agents(request), {"agents": []}))["agents"]
        items += technical(dlq_total, agents)

    ranked = rank(items, role=claims.get("role"), niche=scope)
    groups = {p: [asdict(i) | {"score": round(i.score, 1)} for i in ranked if i.priority == p] for p in ("now", "today", "week")}
    return {"now": groups["now"], "today": groups["today"], "week": groups["week"], "count": len(ranked),
            "generated_at": now.isoformat()}


async def _below(db, analytics, pricing) -> int:
    return sum(len(v) for v in (await analytics._below_target(db, await pricing.context(db))).values())


async def _dlq_total(bus) -> int:
    return int((await bus.js.stream_info("DLQ")).state.messages or 0)


# ---------------------------------------------------------------- the summary above the list (KPIs and charts)

TZ = "Asia/Jerusalem"
OPEN_STATES = ("LEAD", "RECEIVED", "PERSONALIZING", "SCHEDULING", "PRINTING", "QC", "NEEDS_ATTENTION")
PRINTING = ("printing", "paused", "calibrating")


def utilization(printers: list[dict], states: dict[str, str]) -> dict:
    """How many printers are online and how many of them are working (printing, paused mid-print, calibrating)."""
    online = [p for p in printers if p.get("online")]
    busy = [p for p in online if states.get(p["printer_id"]) in PRINTING or (p.get("busy") and p["printer_id"] not in states)]
    pct = round(len(busy) * 100 / len(online)) if online else 0
    return {"total": len(printers), "online": len(online), "printing": len(busy), "utilization_pct": pct}


def days_series(rows: list[dict], end, days: int = 7) -> list[dict]:
    """One point per day (oldest first), zero for days without rows."""
    by = {str(r["day"]): r for r in rows}
    out = []
    for n in range(days - 1, -1, -1):
        d = str(end - timedelta(days=n))
        r = by.get(d, {})
        out.append({"day": d, "orders": int(r.get("orders") or 0),
                    "revenue_agorot": int(r["revenue_agorot"]) if r.get("revenue_agorot") is not None else 0})
    return out


@router.get("/api/v1/today/summary")
async def summary(request: Request, niche: str | None = None, claims: dict = Depends(auth.require_auth)):
    """The KPI strip and the two charts of Today: printers at work, today's orders and revenue, stock alerts,
    7 days of revenue and orders, open orders per stage and (operators) revenue per niche. ?niche= filters the
    orders and the money; printers and alerts are the whole farm."""
    db, bus = request.app.state.db, request.app.state.bus
    scope = niche_scope(niche)
    money = auth.allows(claims.get("role"), "operator")
    end = datetime.now(UTC).astimezone(ZoneInfo(TZ)).date()
    in_niche = (" AND EXISTS (SELECT 1 FROM coordinator.order_items n WHERE n.order_id = o.order_id AND n.niche = %s)"
                if scope else "")
    np = (scope,) if scope else ()

    printers = await _safe(lambda: db.fetchall("SELECT printer_id, online, busy FROM fleet.printers"), [])
    states: dict[str, str] = {}
    for p in printers:
        raw = await _safe(lambda pid=p["printer_id"]: bus.kv_get("printer_status", pid), None)
        if raw:
            try:
                st = json.loads(raw)
                states[p["printer_id"]] = (st.get("data") or {}).get("status") or ""
            except ValueError:
                pass

    orders_by_day = await _safe(lambda: db.fetchall(
        f"SELECT (o.created_at AT TIME ZONE '{TZ}')::date AS day, count(*) AS orders FROM coordinator.orders o "
        f"WHERE o.channel <> 'stock' AND o.created_at > now() - interval '8 days'{in_niche} GROUP BY 1", np), [])
    revenue_by_day = await _safe(lambda: db.fetchall(
        "SELECT entry_date AS day, sum(net_agorot) AS revenue_agorot FROM reporting.niche_revenue_v "
        "WHERE entry_date > %s" + (" AND niche = %s" if scope else "") + " GROUP BY 1",
        (end - timedelta(days=7), *np)), []) if money else []
    merged: dict[str, dict] = {}
    for r in orders_by_day:
        merged.setdefault(str(r["day"]), {"day": r["day"]})["orders"] = r["orders"]
    for r in revenue_by_day:
        merged.setdefault(str(r["day"]), {"day": r["day"]})["revenue_agorot"] = r["revenue_agorot"]
    week = days_series(list(merged.values()), end)

    stages = await _safe(lambda: db.fetchall(
        "SELECT s.state, count(*) AS n FROM coordinator.orders o JOIN coordinator.sagas s ON s.saga_id = o.order_id "
        f"WHERE o.channel <> 'stock' AND s.state = ANY(%s){in_niche} GROUP BY 1", (list(OPEN_STATES), *np)), [])
    by_stage = {r["state"]: int(r["n"]) for r in stages}
    niches = await _safe(lambda: db.fetchall(
        "SELECT v.niche, coalesce(n.name, v.niche) AS name, coalesce(n.color, 8) AS color, sum(v.net_agorot) AS revenue_agorot "
        "FROM reporting.niche_revenue_v v LEFT JOIN catalog.niches n ON n.slug = v.niche WHERE v.entry_date > %s "
        "GROUP BY 1, 2, 3 HAVING sum(v.net_agorot) <> 0 ORDER BY 4 DESC", (end - timedelta(days=7),)), []) if money else []

    alerts = await _safe(lambda: db.fetchall(
        "SELECT kind, count(*) AS n FROM inventory.alerts WHERE resolved_at IS NULL GROUP BY 1"), [])
    shelf_low = await _safe(lambda: db.fetchval(
        "SELECT count(*) FROM coordinator.finished_goods WHERE min_stock > 0 AND on_hand - reserved < min_stock"), 0)
    alert_kinds = {r["kind"]: int(r["n"]) for r in alerts}
    today_row = week[-1]
    return {
        "printers": utilization(printers, states),
        "orders_today": today_row["orders"],
        "revenue_today_agorot": today_row["revenue_agorot"] if money else None,
        "alerts": {"filament": alert_kinds.get("low_stock", 0) + alert_kinds.get("spool_short", 0)
                   + alert_kinds.get("spool_empty", 0), "parts": alert_kinds.get("low_item", 0),
                   "shelf": int(shelf_low or 0)},
        "week": [{**d, "revenue_agorot": d["revenue_agorot"] if money else None} for d in week],
        "stages": [{"state": s, "count": by_stage.get(s, 0)} for s in OPEN_STATES],
        "niches": [{"niche": r["niche"], "name": r["name"], "color": r["color"], "revenue_agorot": int(r["revenue_agorot"])}
                   for r in niches],
        "money": money,
    }

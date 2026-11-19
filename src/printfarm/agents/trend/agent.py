"""Trend Analyst (Wave 1 / M6, phase 10b): the daily analytics and what is rising or falling.

Every TREND_INTERVAL_S (an hour) it rebuilds yesterday and today in reporting.daily_sku / daily_niche (idempotent:
a day's rows are replaced) and the day's signals (trend.signals, evt.trend.signal.detected): niches and SKUs whose
sales or social intent rise or fall (signals.py). On its first start it backfills the days that have no roll-up
yet (BACKFILL_DAYS). cmd.trend.backfill rebuilds any number of days on demand.

With the trend.enabled setting on, once a day it asks Claude for a short Hebrew insight per niche (insight.py: one
cheap call, aggregated numbers only, capped at DAILY_BUDGET_USD a day).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.social.llm import LLMError
from printfarm.agents.trend.insight import DAILY_BUDGET_USD, MODEL, make_trend_llm
from printfarm.agents.trend.rollup import DAILY_NICHE, DAILY_SKU, NICHE_WINDOWS, SKU_WINDOWS, TZ
from printfarm.agents.trend.signals import Window, detect
from printfarm.contracts import deterministic_id
from printfarm.contracts.messages import AnalyticsRolledUp, BackfillAnalytics, TrendSignal

BACKFILL_DAYS = 120


def business_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(TZ)).date()


class TrendAgent(Agent):
    name = "trend"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interval_s = float(os.getenv("TREND_INTERVAL_S", "3600"))
        self.llm = make_trend_llm()
        self._last: dict = {}

    def heartbeat_info(self) -> dict:
        return {**self._last, "insights": "on" if self.setting("trend.enabled") else "off",
                "model": MODEL if self.llm else None}

    async def on_start(self) -> None:
        self.spawn(self._loop())

    async def _loop(self) -> None:
        first = True
        while True:
            try:
                today = business_today()
                if first:
                    await self._backfill_missing(today)
                    first = False
                await self.run(today - timedelta(days=1), today, by="auto")
            except Exception:  # noqa: BLE001 - one failed night must not stop the agent; it retries next interval
                self.log.exception("analytics roll-up failed")
            await asyncio.sleep(self.interval_s)

    async def _backfill_missing(self, today: date) -> None:
        async with self.db.tx() as conn:
            done = {r["day"] for r in await (await conn.execute(
                "SELECT day FROM reporting.rollups WHERE day > %s", (today - timedelta(days=BACKFILL_DAYS),))).fetchall()}
        missing = [today - timedelta(days=n) for n in range(BACKFILL_DAYS, 1, -1)
                   if today - timedelta(days=n) not in done]
        for d in missing:
            async with self.db.tx() as conn:
                await self.roll_day(conn, d)
        if missing:
            self.log.info("analytics backfilled: %d days", len(missing))

    async def roll_day(self, conn, d: date) -> int:
        """Replace one day's analytics. Returns the number of product rows."""
        await conn.execute("DELETE FROM reporting.daily_sku WHERE day = %s", (d,))
        await conn.execute("DELETE FROM reporting.daily_niche WHERE day = %s", (d,))
        rows = (await conn.execute(DAILY_SKU, {"d": d})).rowcount
        await conn.execute(DAILY_NICHE, {"d": d})
        await conn.execute("INSERT INTO reporting.rollups (day, rows) VALUES (%s, %s) ON CONFLICT (day) DO UPDATE "
                           "SET rows = EXCLUDED.rows, rolled_at = now()", (d, rows))
        return max(rows, 0)

    async def run(self, first: date, last: date, *, by: str, cause=None) -> AnalyticsRolledUp:
        """Roll up first..last (inclusive), then the signals (and maybe the insights) of ``last``."""
        rows, d = 0, first
        while d <= last:
            async with self.db.tx() as conn:
                rows += await self.roll_day(conn, d)
            d += timedelta(days=1)
        async with self.db.tx() as conn:
            signals = await self._signals(conn, last, cause)
            done = AnalyticsRolledUp(days=(last - first).days + 1, first_day=first.isoformat(), last_day=last.isoformat(),
                                     rows=rows, signals=signals, by=by)
            await self.emit(conn, done, cause=cause)
        self.relay.wakeup.set()                    # publish the signals now, not at the next relay poll
        insights = await self._insights(last)
        self._last = {"last_rollup": datetime.now(UTC).isoformat(), "last_day": last.isoformat(), "signals": signals,
                      "insights_today": insights}
        return done

    async def _windows(self, conn, d: date) -> list[Window]:
        out = []
        for scope, sql in (("niche", NICHE_WINDOWS), ("sku", SKU_WINDOWS)):
            for r in await (await conn.execute(sql, {"d": d})).fetchall():
                out.append(Window(scope=scope, subject=r["subject"], niche=r["niche"], units_7d=int(r["units_7d"]),
                                  units_prev_7d=int(r["units_prev_7d"]), units_28d=int(r["units_28d"]),
                                  units_prev_28d=int(r["units_prev_28d"]), leads_7d=int(r["leads_7d"]),
                                  leads_prev_7d=int(r["leads_prev_7d"]), revenue_7d=int(r["revenue_7d"])))
        return out

    async def _signals(self, conn, d: date, cause) -> int:
        found = detect(await self._windows(conn, d))
        await conn.execute("DELETE FROM trend.signals WHERE day = %s", (d,))
        for s in found:
            sid = f"sig-{deterministic_id('trend', d.isoformat(), s.topic)[:16]}"
            await conn.execute(
                "INSERT INTO trend.signals (signal_id, topic, direction, strength, evidence, day, scope, subject, niche, "
                "change_pct) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (signal_id) DO UPDATE SET "
                "direction = EXCLUDED.direction, strength = EXCLUDED.strength, evidence = EXCLUDED.evidence, "
                "change_pct = EXCLUDED.change_pct, created_at = now()",
                (sid, s.topic, s.direction, s.strength, Jsonb(list(s.evidence)), d, s.scope, s.subject, s.niche,
                 s.change_pct))
            await self.emit(conn, TrendSignal(signal_id=sid, topic=s.topic, direction=s.direction, strength=s.strength,
                                              evidence=list(s.evidence), day=d.isoformat(), niche=s.niche,
                                              change_pct=s.change_pct), cause=cause)
        return len(found)

    async def _insights(self, d: date) -> int:
        """At most once a day, with the setting on and a model available: one Hebrew paragraph per niche."""
        if not self.setting("trend.enabled") or self.llm is None:
            return 0
        async with self.db.tx() as conn:
            have = await (await conn.execute("SELECT count(*) AS n, coalesce(sum(cost_usd), 0) AS usd FROM trend.insights "
                                             "WHERE day = %s", (d,))).fetchone()
            if have["n"] or float(have["usd"]) >= DAILY_BUDGET_USD:
                return 0
            niches = {r["slug"]: r["name"] for r in await (await conn.execute(
                "SELECT slug, name FROM catalog.niches WHERE NOT archived")).fetchall()}
            windows = [w for w in await self._windows(conn, d) if w.scope == "niche" and w.subject in niches]
            rising = {}
            for r in await (await conn.execute(
                    "SELECT niche, subject FROM trend.signals WHERE day = %s AND scope = 'sku' AND direction = 'rising' "
                    "ORDER BY strength DESC", (d,))).fetchall():
                rising.setdefault(r["niche"], []).append(r["subject"])
        facts = [{"niche": w.subject, "name": niches[w.subject], "units_7d": w.units_7d, "units_prev_7d": w.units_prev_7d,
                  "revenue_7d_ils": round(w.revenue_7d / 100), "leads_7d": w.leads_7d, "rising": rising.get(w.subject, [])}
                 for w in windows if w.units_7d or w.units_prev_7d or w.leads_7d]
        if not facts:
            return 0
        try:
            items, usage = await self.llm.insights(facts)
        except LLMError as exc:
            self.log.warning("trend insights not written: %s", exc)
            return 0
        share = usage.cost / max(len(items), 1)
        async with self.db.tx() as conn:
            for i in items:
                await conn.execute("INSERT INTO trend.insights (day, niche, text, model, cost_usd) VALUES (%s, %s, %s, %s, %s) "
                                   "ON CONFLICT (day, niche) DO NOTHING", (d, i.niche, i.text, usage.model, share))
        self.log.info("trend insights for %s: %d niches, $%.4f", d, len(items), usage.cost)
        return len(items)

    @on("cmd.trend.backfill")
    async def backfill(self, env, cmd: BackfillAnalytics, conn):
        today = business_today()
        # Long rebuilds run after this handler (one transaction per day), not inside it.
        self.after_commit(lambda: self.spawn(self.run(today - timedelta(days=cmd.days - 1), today,
                                                      by=cmd.requested_by or "unknown", cause=env)))

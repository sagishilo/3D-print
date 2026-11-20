"""Scheduling policy (Wave 2 / W2-C), pure: which queued jobs a free printer takes next, in what order, and why;
whether a printer may start now; and a forecast of the next day per printer (the Schedule timeline). The fleet
agent's scheduler and the timeline use the same rules, so the timeline shows what will really happen.

Order of the queue for one printer (each job gets the reason it is where it is):

1. **Held** jobs never start; jobs **pinned** to another printer are not this printer's.
2. A **manual order** (rank, set on the timeline: move to top / up / down) comes first, lowest rank first.
3. **Late or nearly due**: a job whose slack (time to its due date minus its print time) is under URGENT_SLACK_H.
4. **Priority** (higher first).
5. **Overnight window**: in the evening (overnight_from .. quiet_from) long jobs go first, so they print through
   the night; during the day a long job without urgency waits for the evening if shorter work is queued.
6. **Due date**, then first come, first served.

Filament grouping: a printer only takes jobs of the filament loaded on it (no swap mid-queue); jobs no printer can
take show as unplaced with the filament they need (a spool-change suggestion). Quiet hours: no new print starts
between quiet_from and quiet_until (running prints continue).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

from printfarm.agents.fleet.packing import JobSpec, build_plate

URGENT_SLACK_H = 6.0


@dataclass(frozen=True)
class Window:
    """Hours of the business day (local time)."""

    overnight_from: float = 18.0   # from here, long jobs first
    quiet_from: float = 23.0       # quiet hours start (no new prints), when enabled
    quiet_until: float = 7.0
    quiet_enabled: bool = False
    long_job_minutes: float = 240.0


@dataclass(frozen=True)
class QJob:
    """A queued job as the policy sees it."""

    job_id: str
    order_id: str
    material: str
    color: str
    w: float
    d: float
    est_minutes: float
    est_grams: float
    priority: int = 0
    due_by: datetime | None = None
    created_at: datetime | None = None
    whole_plate: bool = False
    hold: bool = False
    pinned_printer: str | None = None
    rank: int | None = None

    def spec(self) -> JobSpec:
        return JobSpec(self.job_id, self.w, self.d, self.est_minutes, self.est_grams, self.priority, self.due_by,
                       self.created_at)


@dataclass(frozen=True)
class QPrinter:
    printer_id: str
    material: str
    color: str
    bed: tuple[float, float]
    free_at: datetime              # now when idle; the running plate's expected end otherwise
    available: bool = True         # online, bed clear (or auto-eject), not calibrating, no blocking maintenance
    why_not: str = ""


def window_from(get: Callable[[str], Any]) -> Window:
    """The window from the automation settings (``get``: key -> value, e.g. ``Agent.setting``)."""
    return Window(overnight_from=float(get("fleet.overnight_from")), quiet_from=float(get("fleet.quiet_from")),
                  quiet_until=float(get("fleet.quiet_until")), quiet_enabled=bool(get("fleet.quiet_hours")),
                  long_job_minutes=float(get("fleet.long_job_hours")) * 60)


def _hour(t: datetime, tz: tzinfo) -> float:
    local = t.astimezone(tz)
    return local.hour + local.minute / 60


def in_range(h: float, start: float, end: float) -> bool:
    """Hour ``h`` within [start, end), the range possibly crossing midnight."""
    return start <= h < end if start <= end else (h >= start or h < end)


def quiet_now(t: datetime, w: Window, tz: tzinfo) -> bool:
    return w.quiet_enabled and in_range(_hour(t, tz), w.quiet_from, w.quiet_until)


def quiet_end(t: datetime, w: Window, tz: tzinfo) -> datetime:
    """The first moment at or after ``t`` outside quiet hours."""
    if not quiet_now(t, w, tz):
        return t
    local = t.astimezone(tz)
    end = local.replace(hour=int(w.quiet_until), minute=int(round((w.quiet_until % 1) * 60)), second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end.astimezone(t.tzinfo)


def evening(t: datetime, w: Window, tz: tzinfo) -> bool:
    end = w.quiet_from if w.quiet_enabled else w.quiet_until
    return in_range(_hour(t, tz), w.overnight_from, end)


def slack_hours(j: QJob, now: datetime) -> float | None:
    if j.due_by is None:
        return None
    return (j.due_by - now).total_seconds() / 3600 - j.est_minutes / 60


@dataclass(frozen=True)
class Ranked:
    job: QJob
    reason: str


def order_for(printer: QPrinter, jobs: Iterable[QJob], now: datetime, w: Window, tz: tzinfo) -> list[Ranked]:
    """This printer's jobs in the order it takes them, each with the reason (see the module docstring)."""
    late = evening(now, w, tz)
    keyed = []
    for j in jobs:
        if j.hold or (j.material, j.color) != (printer.material, printer.color):
            continue
        if j.pinned_printer and j.pinned_printer != printer.printer_id:
            continue
        slack = slack_hours(j, now)
        urgent = slack is not None and slack < URGENT_SLACK_H
        long_ = j.est_minutes >= w.long_job_minutes
        if j.rank is not None:
            tier, reason = 0, f"your order (#{j.rank})"
        elif urgent:
            tier, reason = 1, "late" if slack < 0 else f"due in {max(0.0, slack + j.est_minutes / 60):.0f} h"
        elif long_ and late:
            tier, reason = 2, "long job: printing overnight"
        elif long_:
            tier, reason = 4, "long job: waits for the evening"
        else:
            tier, reason = 3, "priority" if j.priority else "first come, first served"
        if j.pinned_printer and j.rank is None and not urgent:
            reason = f"pinned to {j.pinned_printer} · {reason}"
        far = datetime.max.replace(tzinfo=now.tzinfo)
        keyed.append(((tier, j.rank if j.rank is not None else 0, -j.priority, slack if slack is not None else 1e9,
                       j.due_by or far, j.created_at or far, j.job_id), Ranked(j, reason)))
    return [r for _, r in sorted(keyed, key=lambda x: x[0])]


@dataclass(frozen=True)
class Pick:
    jobs: tuple[str, ...]
    est_minutes: float
    est_grams: float
    reasons: dict[str, str]
    placements: tuple = ()


def pick_plate(printer: QPrinter, ranked: Sequence[Ranked], *, margin: float, spacing: float, max_minutes: float) -> Pick | None:
    """The plate this printer prints next: a sliced file (whole plate) alone when it leads the queue, otherwise the
    leading jobs packed onto one bed. The leading job is always on it (the queue order is respected)."""
    if not ranked:
        return None
    if ranked[0].job.whole_plate:
        j = ranked[0].job
        plan = build_plate([j.spec()], printer.bed, margin=margin, spacing=spacing, max_minutes=max(max_minutes, j.est_minutes))
        if plan is None:
            return None
        return Pick((j.job_id,), j.est_minutes, j.est_grams, {j.job_id: ranked[0].reason}, tuple(plan.placements))
    pool = [r for r in ranked if not r.job.whole_plate]
    # A leader too big for this bed is skipped (another printer may take it); the rest keep the queue order.
    while pool:
        plan = build_plate([r.job.spec() for r in pool], printer.bed, margin=margin, spacing=spacing,
                           max_minutes=max_minutes, ordered=True)
        if plan is None:
            return None
        ids = tuple(p.item_id for p in plan.placements)
        if pool[0].job.job_id in ids:
            reasons = {r.job.job_id: r.reason for r in pool if r.job.job_id in ids}
            return Pick(ids, plan.est_minutes, plan.est_grams, reasons, tuple(plan.placements))
        pool = pool[1:]
    return None


def qjob(r: Mapping[str, Any]) -> QJob:
    """A fleet.jobs row as the policy sees it."""
    return QJob(r["job_id"], r["order_id"], r["material"], r["color"], r["footprint_w"], r["footprint_d"],
                r["est_minutes"], r["est_grams"], r["priority"], r["due_by"], r["created_at"], r["whole_plate"],
                bool(r.get("hold")), r.get("pinned_printer"), r.get("rank"))


@dataclass
class Block:
    printer_id: str
    start: datetime
    end: datetime
    jobs: tuple[str, ...]
    reasons: dict[str, str]
    running: bool = False
    note: str = ""


@dataclass
class Forecast:
    lanes: dict[str, list[Block]] = field(default_factory=dict)
    unplaced: list[dict] = field(default_factory=list)   # {material, color, jobs, minutes}: no printer has it loaded


def forecast(printers: Sequence[QPrinter], jobs: Sequence[QJob], now: datetime, w: Window, tz: tzinfo, *,
             running: Sequence[Block] = (), horizon_h: float = 24, margin: float = 5, spacing: float = 4,
             max_minutes: float = 480) -> Forecast:
    """What each printer prints over the next ``horizon_h`` hours if nothing changes: the running plates, then
    plates picked by the same policy as the scheduler, one printer at a time in order of when it is free."""
    out = Forecast({p.printer_id: [] for p in printers})
    for b in running:
        out.lanes.setdefault(b.printer_id, []).append(b)
    left = {j.job_id: j for j in jobs if not j.hold}
    free = {p.printer_id: max(p.free_at, now) for p in printers if p.available}
    by_id = {p.printer_id: p for p in printers}
    horizon = now + timedelta(hours=horizon_h)
    while free and left:
        pid = min(free, key=lambda k: (free[k], k))
        start = quiet_end(free[pid], w, tz)
        if start >= horizon:
            break
        p = by_id[pid]
        ranked = order_for(p, left.values(), start, w, tz)
        pick = pick_plate(p, ranked, margin=margin, spacing=spacing, max_minutes=max_minutes)
        if pick is None:
            del free[pid]
            continue
        end = start + timedelta(minutes=pick.est_minutes)
        note = "after quiet hours" if start > free[pid] else ""
        out.lanes[pid].append(Block(pid, start, end, pick.jobs, pick.reasons, note=note))
        for jid in pick.jobs:
            left.pop(jid, None)
        free[pid] = end
    groups: dict[tuple[str, str], list[QJob]] = {}
    for j in left.values():
        if not any((p.material, p.color) == (j.material, j.color) for p in printers):
            groups.setdefault((j.material, j.color), []).append(j)
    out.unplaced = [{"material": m, "color": c, "jobs": [j.job_id for j in js], "minutes": round(sum(j.est_minutes for j in js))}
                    for (m, c), js in sorted(groups.items(), key=lambda kv: -sum(j.est_minutes for j in kv[1]))]
    return out

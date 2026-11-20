"""Printer maintenance (Wave 2 / W2-C), pure: interval tasks against a printer's counters.

A task repeats every N print hours, N plates and/or N days (whichever comes first). It is *due* from DUE_AT of its
interval used and *overdue* past 100 %. A task marked ``block_when_overdue`` keeps the printer out of the schedule
while it is overdue (the running plate finishes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DUE_AT = 0.9

# Seeded once per printer; the owner can change or archive them (Fleet > printer > Maintenance).
DEFAULT_TASKS: tuple[dict, ...] = (
    {"task_id": "clean-plate", "label": "Clean the build plate (soap / IPA)", "every_plates": 25},
    {"task_id": "nozzle-check", "label": "Check the nozzle and wipe the hotend", "every_hours": 100},
    {"task_id": "lube-rods", "label": "Lubricate rods / rails, check belts", "every_hours": 300, "every_days": 90},
)

# Added for Bambu Lab A1 / A1 mini (bed-slinger, quick-swap hotend, filament cutter in the toolhead).
A1_TASKS: tuple[dict, ...] = (
    {"task_id": "hotend-wear", "label": "Inspect the nozzle for wear; swap the hotend if worn", "every_hours": 500},
    {"task_id": "cutter-ptfe", "label": "Check the filament cutter and the PTFE tube", "every_hours": 300},
    {"task_id": "bed-cable", "label": "Check the heatbed cable and its strain relief", "every_days": 90},
)
MODEL_TASKS: dict[str, tuple[dict, ...]] = {"a1": A1_TASKS, "a1-mini": A1_TASKS}


def tasks_for(model: str) -> tuple[dict, ...]:
    """The tasks a new printer of this model starts with (fleet.toml ``model``)."""
    return DEFAULT_TASKS + MODEL_TASKS.get((model or "").strip().lower(), ())


@dataclass(frozen=True)
class Counters:
    print_seconds: int
    plates: int


@dataclass(frozen=True)
class Task:
    printer_id: str
    task_id: str
    label: str
    every_hours: float | None = None
    every_plates: int | None = None
    every_days: int | None = None
    block_when_overdue: bool = False
    last_done_at: datetime | None = None
    last_done_seconds: int = 0
    last_done_plates: int = 0


@dataclass(frozen=True)
class TaskStatus:
    state: str              # "ok" | "due" | "overdue"
    used: float             # share of the interval used (the most used of its intervals), 0..
    left: str               # what is left (or over), e.g. "12 h", "3 plates", "5 days"
    blocks: bool            # overdue and blocking: no new plates for this printer


def status(t: Task, c: Counters, now: datetime, since: datetime | None = None) -> TaskStatus:
    """``since``: when counting started (the printer was added), for a task never done."""
    parts: list[tuple[float, str]] = []
    if t.every_hours:
        h = max(0, c.print_seconds - t.last_done_seconds) / 3600
        parts.append((h / t.every_hours, f"{t.every_hours - h:.0f} h"))
    if t.every_plates:
        n = max(0, c.plates - t.last_done_plates)
        parts.append((n / t.every_plates, f"{t.every_plates - n} plates"))
    if t.every_days:
        start = t.last_done_at or since
        d = (now - start).total_seconds() / 86400 if start else 0.0
        parts.append((d / t.every_days, f"{t.every_days - d:.0f} days"))
    used, left = max(parts, key=lambda p: p[0]) if parts else (0.0, "")
    state = "overdue" if used >= 1 else "due" if used >= DUE_AT else "ok"
    return TaskStatus(state, round(used, 3), left.replace("-", "") if used >= 1 else left,
                      state == "overdue" and t.block_when_overdue)


def crossed(before: str, after: str) -> bool:
    """A task moved to a worse state (ok -> due, due -> overdue, ok -> overdue): tell someone once."""
    rank = {"ok": 0, "due": 1, "overdue": 2}
    return rank[after] > rank[before]

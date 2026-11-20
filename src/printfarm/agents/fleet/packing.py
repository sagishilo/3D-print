"""Bin packing for build plates.

Level 1 - plate packing (this module): place rectangular part footprints on a
printer bed with MaxRects + Best-Short-Side-Fit, allowing 90-degree rotation.
Each footprint is inflated by the part spacing so parts never touch.

Level 2 - plate-to-printer assignment (``build_plate``): for one free printer,
take compatible queued jobs in priority order and greedily add each one if it
still fits on the bed and within the plate's time budget (first-fit
decreasing within a priority band).

With 3-5 printers the greedy assignment is near-optimal and runs in
microseconds. The upgrade path to OR-Tools CP-SAT (global assignment across
printers, due-date penalties, operator shifts) is in ADR-0005.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    d: float

    def contains(self, o: Rect) -> bool:
        return o.x >= self.x and o.y >= self.y and o.x + o.w <= self.x + self.w and o.y + o.d <= self.y + self.d

    def intersects(self, o: Rect) -> bool:
        return not (o.x >= self.x + self.w or o.x + o.w <= self.x or o.y >= self.y + self.d or o.y + o.d <= self.y)


@dataclass(frozen=True)
class Placement:
    item_id: str
    x: float
    y: float
    w: float
    d: float
    rotated: bool


class MaxRectsBin:
    def __init__(self, width: float, depth: float, margin: float = 0.0, spacing: float = 0.0):
        self.spacing = spacing
        # Usable area excludes the edge margin; spacing is added to each part.
        self.free: list[Rect] = [Rect(margin, margin, width - 2 * margin + spacing, depth - 2 * margin + spacing)]
        self.placements: list[Placement] = []

    def insert(self, item_id: str, w: float, d: float, allow_rotate: bool = True) -> Placement | None:
        best: tuple[float, float, Rect, bool] | None = None
        options = [(w, d, False)] + ([(d, w, True)] if allow_rotate and w != d else [])
        for fw, fd, rot in options:
            iw, idp = fw + self.spacing, fd + self.spacing
            for fr in self.free:
                if iw <= fr.w + 1e-9 and idp <= fr.d + 1e-9:
                    short, long_ = sorted((fr.w - iw, fr.d - idp))
                    score = (short, long_)
                    if best is None or score < best[:2]:
                        best = (short, long_, Rect(fr.x, fr.y, iw, idp), rot)
        if best is None:
            return None
        node, rot = best[2], best[3]
        self._split(node)
        p = Placement(item_id, node.x, node.y, node.w - self.spacing, node.d - self.spacing, rot)
        self.placements.append(p)
        return p

    def _split(self, used: Rect) -> None:
        new_free: list[Rect] = []
        for fr in self.free:
            if not fr.intersects(used):
                new_free.append(fr)
                continue
            if used.x > fr.x:
                new_free.append(Rect(fr.x, fr.y, used.x - fr.x, fr.d))
            if used.x + used.w < fr.x + fr.w:
                new_free.append(Rect(used.x + used.w, fr.y, fr.x + fr.w - used.x - used.w, fr.d))
            if used.y > fr.y:
                new_free.append(Rect(fr.x, fr.y, fr.w, used.y - fr.y))
            if used.y + used.d < fr.y + fr.d:
                new_free.append(Rect(fr.x, used.y + used.d, fr.w, fr.y + fr.d - used.y - used.d))
        # prune rectangles contained in others
        self.free = [
            r for i, r in enumerate(new_free)
            if not any(j != i and o.contains(r) and (o != r or j < i) for j, o in enumerate(new_free))
        ]

    def utilization(self, width: float, depth: float) -> float:
        return sum(p.w * p.d for p in self.placements) / (width * depth)


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    w: float
    d: float
    est_minutes: float
    est_grams: float
    priority: int = 0
    due_by: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PlatePlan:
    placements: list[Placement]
    est_minutes: float
    est_grams: float
    utilization: float


def job_order_key(j: JobSpec):
    far = datetime.max.replace(tzinfo=UTC)
    return (-j.priority, j.due_by or far, -(j.w * j.d), j.created_at or far)


def build_plate(
    jobs: list[JobSpec],
    bed: tuple[float, float],
    *,
    margin: float = 5,
    spacing: float = 4,
    max_minutes: float = 480,
    ordered: bool = False,
) -> PlatePlan | None:
    """Pack as many compatible jobs as fit onto one plate. None if nothing fits. ``ordered``: the jobs are already in
    the order to take them (the scheduling policy's); otherwise they are sorted by ``job_order_key``."""
    bin_ = MaxRectsBin(bed[0], bed[1], margin, spacing)
    minutes = grams = 0.0
    for j in (jobs if ordered else sorted(jobs, key=job_order_key)):
        # A plate's print time ~ sum of its parts (minus shared heat-up; ignored here).
        if bin_.placements and minutes + j.est_minutes > max_minutes:
            continue
        if bin_.insert(j.job_id, j.w, j.d):
            minutes += j.est_minutes
            grams += j.est_grams
    if not bin_.placements:
        return None
    return PlatePlan(bin_.placements, minutes, grams, bin_.utilization(*bed))

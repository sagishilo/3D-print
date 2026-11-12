from itertools import combinations

from printfarm.agents.fleet.packing import JobSpec, MaxRectsBin, Rect, build_plate


def overlaps(a, b, spacing=0.0):
    ra = Rect(a.x, a.y, a.w + spacing, a.d + spacing)
    rb = Rect(b.x, b.y, b.w + spacing, b.d + spacing)
    return ra.intersects(rb)


def test_nameplates_fill_a_bambu_bed_without_overlap():
    jobs = [JobSpec(f"j{i}", 120, 35, est_minutes=20, est_grams=10) for i in range(20)]
    plan = build_plate(jobs, (256, 256), margin=5, spacing=4)
    assert plan is not None
    # 120x35 + 4mm spacing in a 246x246 usable area: 2 columns x 6 rows = 12
    assert len(plan.placements) == 12
    for a, b in combinations(plan.placements, 2):
        assert not overlaps(a, b, spacing=4)
    for p in plan.placements:
        assert p.x >= 5 and p.y >= 5 and p.x + p.w <= 251 and p.y + p.d <= 251


def test_time_budget_limits_plate():
    jobs = [JobSpec(f"j{i}", 50, 50, est_minutes=200, est_grams=30) for i in range(5)]
    plan = build_plate(jobs, (256, 256), max_minutes=480)
    assert len(plan.placements) == 2 and plan.est_minutes == 400


def test_rotation_used_when_needed():
    b = MaxRectsBin(100, 300)
    p = b.insert("tall", 250, 80)
    assert p is not None and p.rotated


def test_priority_and_due_date_order():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    jobs = [
        JobSpec("late", 200, 200, 10, 10, due_by=now + timedelta(days=5)),
        JobSpec("urgent", 200, 200, 10, 10, due_by=now + timedelta(hours=2)),
    ]
    plan = build_plate(jobs, (256, 256))
    assert [p.item_id for p in plan.placements] == ["urgent"]


def test_nothing_fits():
    assert build_plate([JobSpec("huge", 400, 400, 10, 10)], (256, 256)) is None

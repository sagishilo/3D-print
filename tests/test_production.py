"""Wave 2 / W2-C, pure parts: the scheduling policy and forecast, the manual order, maintenance states, the camera
watch, the new notification rules, the Today items and the Schedule read model."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from printfarm.agents.fleet import maintenance as mt
from printfarm.agents.fleet.agent import manual_order
from printfarm.agents.fleet.policy import (
    QJob,
    QPrinter,
    Window,
    evening,
    forecast,
    in_range,
    order_for,
    pick_plate,
    quiet_end,
    quiet_now,
    window_from,
)
from printfarm.agents.integrations.alerts import for_event
from printfarm.agents.telemetry.vision import FakeScorer, Watch
from printfarm.contracts.config_keys import CONFIG_DEFAULTS
from printfarm.contracts.messages import ControlJob, UpsertMaintenanceTask

TZ = ZoneInfo("Asia/Jerusalem")
W = Window()
WQ = Window(quiet_enabled=True)


def at(h: int, m: int = 0, day: int = 20) -> datetime:
    return datetime(2026, 11, day, h, m, tzinfo=TZ).astimezone(UTC)


def job(jid: str, minutes: float = 60, **kw) -> QJob:
    base = {"order_id": "o-" + jid, "material": "PLA", "color": "black", "w": 40, "d": 40, "est_minutes": minutes,
            "est_grams": 10, "created_at": at(8)}
    return QJob(jid, **(base | kw))


P1 = QPrinter("p1", "PLA", "black", (256, 256), at(9))
P2 = QPrinter("p2", "PLA", "white", (256, 256), at(9))


def ids(ranked) -> list[str]:
    return [r.job.job_id for r in ranked]


# ---------------------------------------------------------------- windows


def test_hour_ranges_cross_midnight():
    assert in_range(23.5, 23, 7) and in_range(3, 23, 7) and not in_range(12, 23, 7)
    assert in_range(19, 18, 23) and not in_range(23, 18, 23)


def test_quiet_hours_only_when_enabled_and_end_at_quiet_until():
    assert not quiet_now(at(23, 30), W, TZ)
    assert quiet_now(at(23, 30), WQ, TZ) and quiet_now(at(2), WQ, TZ) and not quiet_now(at(7), WQ, TZ)
    assert quiet_end(at(23, 30), WQ, TZ) == at(7, day=21)
    assert quiet_end(at(2), WQ, TZ) == at(7)
    assert quiet_end(at(12), WQ, TZ) == at(12)


def test_evening_runs_to_quiet_hours_or_morning():
    assert evening(at(19), W, TZ) and evening(at(2), W, TZ) and not evening(at(9), W, TZ)
    assert evening(at(19), WQ, TZ) and not evening(at(23, 30), WQ, TZ)


def test_window_from_settings_defaults():
    w = window_from(CONFIG_DEFAULTS.__getitem__)
    assert w == Window(overnight_from=18, quiet_from=23, quiet_until=7, quiet_enabled=False, long_job_minutes=240)


# ---------------------------------------------------------------- order for one printer


def test_filament_hold_and_pins_filter_the_queue():
    jobs = [job("a"), job("b", color="white"), job("c", hold=True), job("d", pinned_printer="p9"), job("e", pinned_printer="p1")]
    assert ids(order_for(P1, jobs, at(9), W, TZ)) == ["e", "a"] or ids(order_for(P1, jobs, at(9), W, TZ)) == ["a", "e"]
    assert "c" not in ids(order_for(P1, jobs, at(9), W, TZ))
    assert ids(order_for(P2, jobs, at(9), W, TZ)) == ["b"]


def test_manual_order_beats_due_dates_and_priority():
    jobs = [job("late", due_by=at(10)), job("vip", priority=5), job("mine", rank=1)]
    r = order_for(P1, jobs, at(9), W, TZ)
    assert ids(r) == ["mine", "late", "vip"]
    assert r[0].reason == "your order (#1)" and r[1].reason.startswith("due in") and r[2].reason == "priority"


def test_late_jobs_say_late():
    r = order_for(P1, [job("x", due_by=at(8))], at(9), W, TZ)
    assert r[0].reason == "late"


def test_long_jobs_wait_for_the_evening_during_the_day_and_lead_at_night():
    jobs = [job("long", minutes=600, created_at=at(1)), job("short", minutes=30, created_at=at(8))]
    day = order_for(P1, jobs, at(10), W, TZ)
    assert ids(day) == ["short", "long"] and day[1].reason == "long job: waits for the evening"
    night = order_for(P1, jobs, at(19), W, TZ)
    assert ids(night) == ["long", "short"] and night[0].reason == "long job: printing overnight"


def test_an_urgent_long_job_is_not_held_back():
    jobs = [job("long", minutes=300, due_by=at(16)), job("short", minutes=30)]
    assert ids(order_for(P1, jobs, at(10), W, TZ)) == ["long", "short"]


# ---------------------------------------------------------------- plates


def test_pick_plate_keeps_the_leader_and_fills_the_bed_in_queue_order():
    ranked = order_for(P1, [job("a", rank=1), job("b"), job("c")], at(9), W, TZ)
    pick = pick_plate(P1, ranked, margin=5, spacing=4, max_minutes=480)
    assert pick.jobs[0] == "a" and set(pick.jobs) == {"a", "b", "c"} and pick.est_minutes == 180
    assert pick.reasons["a"] == "your order (#1)"


def test_pick_plate_skips_a_leader_too_big_for_this_bed():
    ranked = order_for(P1, [job("huge", w=400, d=400, rank=1), job("b")], at(9), W, TZ)
    assert pick_plate(P1, ranked, margin=5, spacing=4, max_minutes=480).jobs == ("b",)


def test_a_whole_plate_job_prints_alone():
    ranked = order_for(P1, [job("w", whole_plate=True, rank=1), job("b")], at(9), W, TZ)
    pick = pick_plate(P1, ranked, margin=5, spacing=4, max_minutes=480)
    assert pick.jobs == ("w",) and len(pick.placements) == 1


# ---------------------------------------------------------------- forecast


def test_forecast_plans_each_printer_and_lists_missing_filament():
    jobs = [job("a", minutes=200, w=200, d=200), job("b", minutes=60, w=200, d=200), job("w1", color="white"),
            job("r1", color="red", minutes=90), job("r2", color="red", minutes=30)]
    fc = forecast([P1, P2], jobs, at(9), W, TZ)
    lane = fc.lanes["p1"]
    assert [b.jobs for b in lane] == [("a",), ("b",)]
    assert lane[0].start == at(9) and lane[1].start == lane[0].end
    assert [b.jobs for b in fc.lanes["p2"]] == [("w1",)]
    assert fc.unplaced == [{"material": "PLA", "color": "red", "jobs": ["r1", "r2"], "minutes": 120}]


def test_forecast_waits_for_quiet_hours_to_end_and_respects_running_plates():
    busy = QPrinter("p1", "PLA", "black", (256, 256), at(23, 30))
    fc = forecast([busy], [job("a")], at(21), WQ, TZ)
    assert fc.lanes["p1"][0].start == at(7, day=21) and fc.lanes["p1"][0].note == "after quiet hours"
    fc = forecast([busy], [job("a")], at(21), W, TZ)
    assert fc.lanes["p1"][0].start == at(23, 30) and not fc.lanes["p1"][0].note


def test_forecast_skips_unavailable_printers_and_held_jobs():
    off = QPrinter("p1", "PLA", "black", (256, 256), at(9), available=False, why_not="offline")
    fc = forecast([off], [job("a"), job("h", hold=True)], at(9), W, TZ)
    assert fc.lanes["p1"] == [] and fc.unplaced == []


def test_forecast_stops_at_the_horizon():
    jobs = [job(f"j{i}", minutes=300, w=200, d=200) for i in range(10)]
    fc = forecast([P1], jobs, at(9), W, TZ, horizon_h=12)
    assert len(fc.lanes["p1"]) == 3      # 09:00, 14:00, 19:00; 00:00 is past the horizon


# ---------------------------------------------------------------- manual order and commands


@pytest.mark.parametrize(("ranked", "job_id", "action", "out"), [
    (["a", "b", "c"], "c", "top", ["c", "a", "b"]),
    (["a", "b", "c"], "b", "up", ["b", "a", "c"]),
    (["a", "b", "c"], "a", "up", ["a", "b", "c"]),
    (["a", "b", "c"], "b", "down", ["a", "c", "b"]),
    (["a", "b", "c"], "c", "down", ["a", "b", "c"]),
    (["a", "b"], "x", "up", ["a", "b", "x"]),
    (["a", "b"], "x", "top", ["x", "a", "b"]),
    (["a", "b", "c"], "b", "auto", ["a", "c"]),
])
def test_manual_order(ranked, job_id, action, out):
    assert manual_order(ranked, job_id, action) == out


def test_contracts_validate():
    with pytest.raises(ValueError, match="pin needs a printer_id"):
        ControlJob(job_id="j", action="pin")
    with pytest.raises(ValueError, match="at least one interval"):
        UpsertMaintenanceTask(printer_id="p1", task_id="t", label="x")
    with pytest.raises(ValueError):
        UpsertMaintenanceTask(printer_id="p1", task_id="Bad Id", label="x", every_days=3)


# ---------------------------------------------------------------- maintenance


def test_maintenance_states_take_the_most_used_interval():
    now = at(12)
    t = mt.Task("p1", "lube", "Lube", every_hours=100, every_days=90, last_done_at=now - timedelta(days=10),
                last_done_seconds=0)
    assert mt.status(t, mt.Counters(50 * 3600, 0), now).state == "ok"
    s = mt.status(t, mt.Counters(95 * 3600, 0), now)
    assert s.state == "due" and s.left == "5 h"
    s = mt.status(t, mt.Counters(120 * 3600, 0), now)
    assert s.state == "overdue" and s.left == "20 h" and not s.blocks
    plates = mt.Task("p1", "clean", "Clean", every_plates=25, block_when_overdue=True, last_done_plates=10)
    s = mt.status(plates, mt.Counters(0, 36), now)
    assert s.state == "overdue" and s.blocks and s.left == "1 plates"
    days = mt.Task("p1", "belts", "Belts", every_days=30, last_done_at=now - timedelta(days=31))
    assert mt.status(days, mt.Counters(0, 0), now).state == "overdue"


def test_maintenance_crossings_announce_only_worse_states():
    assert mt.crossed("ok", "due") and mt.crossed("due", "overdue") and mt.crossed("ok", "overdue")
    assert not mt.crossed("overdue", "ok") and not mt.crossed("due", "due")


def test_default_tasks_are_valid_commands():
    for t in mt.DEFAULT_TASKS:
        UpsertMaintenanceTask(printer_id="p1", **t)


# ---------------------------------------------------------------- camera watch


def test_watch_needs_frames_in_a_row_then_cools_down():
    w = Watch(threshold=0.7, frames=3, cooldown_s=600)
    assert [w.feed("p1", s, t) for t, s in enumerate([0.9, 0.9, 0.2, 0.9, 0.9])] == [False] * 5
    assert w.feed("p1", 0.95, 5) is True
    assert [w.feed("p1", 0.9, t) for t in (6, 7, 8)] == [False, False, False]   # cooling down
    assert w.feed("p1", 0.9, 700) is True                                       # still failing after the cooldown
    assert w.feed("p2", 0.9, 1) is False and w.run("p2") == 1
    w.reset("p2")
    assert w.run("p2") == 0


def test_fake_scorer_reads_its_scores():
    s = FakeScorer.from_env("bambu-01=0.9, voron-01=x, bad, p3=2")
    assert asyncio.run(s.score("bambu-01", None)) == 0.9
    assert asyncio.run(s.score("voron-01", None)) == 0.0 and asyncio.run(s.score("p3", None)) == 1.0


# ---------------------------------------------------------------- notifications


def test_new_notification_rules():
    n = for_event("printer.failure.suspected", {"printer_id": "p1", "failure_id": 7, "score": 0.86, "frames": 3,
                                                "action_taken": "suggested"}, date(2026, 11, 20))
    assert n.severity == "critical" and "86%" in n.body and "pause it" in n.body and n.dedupe_key == "suspect:7"
    assert for_event("fleet.maintenance.due", {"printer_id": "p1", "task_id": "t", "label": "L", "overdue": False},
                     date(2026, 11, 20)) is None
    n = for_event("fleet.maintenance.due", {"printer_id": "p1", "task_id": "t", "label": "L", "overdue": True,
                                            "blocks": True}, date(2026, 11, 20))
    assert n.topic == "maintenance" and "no new plates" in n.body
    assert for_event("fleet.plate_stack.empty", {"printer_id": "p1"}, date(2026, 11, 20)).topic == "plate_stack"


# ---------------------------------------------------------------- Today items and the Schedule read model


def _gateway(name: str):
    import importlib

    for dep in ("fastapi", "jwt", "argon2", "pydantic_settings"):
        pytest.importorskip(dep)
    import gateway_support  # noqa: F401 - sets the gateway's environment and import path

    return importlib.import_module(f"app.{name}")


def test_today_production_items():
    today = _gateway("today")

    now = at(12)
    s = today.suspicions([{"id": 4, "printer_id": "p1", "message": "camera: failure likely", "action_taken": "suggested",
                           "score": 0.9, "detected_at": now}], now)
    assert s[0].priority == "now" and s[0].action["name"] == "printers.pause"
    assert s[0].meta["alt"]["name"] == "printers.dismiss_suspicion" and s[0].meta["alt"]["body"] == {"failure_id": 4}
    paused = today.suspicions([{"id": 5, "printer_id": "p1", "message": "m", "action_taken": "pause_sent", "score": 0.9}], now)
    assert paused[0].action["type"] == "link"
    tasks = [{"printer_id": "p1", "task_id": "a", "label": "A", "state": "ok", "left": "", "blocks": False},
             {"printer_id": "p1", "task_id": "b", "label": "B", "state": "due", "left": "2 h", "blocks": False},
             {"printer_id": "p1", "task_id": "c", "label": "C", "state": "overdue", "left": "3 plates", "blocks": True}]
    m = today.maintenance(tasks)
    assert [(i.id, i.priority) for i in m] == [("maint:p1:b", "week"), ("maint:p1:c", "now")]
    assert m[1].action["name"] == "maintenance.done"
    st = today.plate_stacks([{"printer_id": "p1", "auto_eject": True, "plate_stack": 6, "plates_left": 0},
                             {"printer_id": "p2", "auto_eject": True, "plate_stack": 6, "plates_left": 2},
                             {"printer_id": "p3", "auto_eject": False, "plate_stack": 0, "plates_left": 0}])
    assert [i.id for i in st] == ["stack:p1"] and st[0].action["body"] == {"printer_id": "p1", "refill": True}
    sp = today.spool_changes([{"material": "PETG", "color": "red", "jobs": ["a", "b"], "minutes": 150}])
    assert sp[0].title == "Load PETG red on a printer" and "2.5 h" in sp[0].why


def _printer(pid, color="black", **kw):
    return {"printer_id": pid, "material": "PLA", "color": color, "bed_w": 256, "bed_d": 256, "online": True,
            "busy": False, "bed_clear": True, "calibrating": False, "auto_eject": False, "plate_stack": 0,
            "plates_left": 0} | kw


def _job(jid, **kw):
    return {"job_id": jid, "order_id": "o-" + jid, "material": "PLA", "color": "black", "footprint_w": 200,
            "footprint_d": 200, "est_minutes": 60, "est_grams": 10, "priority": 0, "due_by": None, "created_at": at(8),
            "whole_plate": False, "hold": False, "pinned_printer": None, "rank": None, "product_type": "nameplate"} | kw


def test_schedule_build_lanes_queue_and_reasons():
    build = _gateway("schedule").build

    now = at(9)
    printers = [_printer("p1", busy=True), _printer("p2", online=False), _printer("p3", color="white", bed_clear=False)]
    running = [{"plate_id": "pl-1", "printer_id": "p1", "status": "printing", "started_at": now - timedelta(minutes=30),
                "est_minutes": 90, "job_id": "r1"}]
    jobs = [_job("a"), _job("b", rank=1), _job("h", hold=True), _job("w", color="white"), _job("g", color="green")]
    out = build(printers, jobs, running, set(), now, W, TZ)
    lanes = {lane["printer_id"]: lane for lane in out["lanes"]}
    assert lanes["p1"]["blocks"][0]["running"] and lanes["p1"]["blocks"][0]["end"] == (now + timedelta(minutes=60)).isoformat()
    assert [b["jobs"][0]["job_id"] for b in lanes["p1"]["blocks"][1:]] == ["b", "a"]
    assert lanes["p2"]["why_not"] == "offline" and not lanes["p2"]["available"]
    assert lanes["p3"]["why_not"] == "bed" and lanes["p3"]["blocks"][0]["jobs"][0]["job_id"] == "w"
    q = {r["job_id"]: r for r in out["queue"]}
    assert [r["job_id"] for r in out["queue"]][:1] == ["b"] and q["b"]["reason"] == "your order (#1)"
    assert q["h"]["reason"] == "held" and q["g"]["reason"] == "no printer has PLA green loaded"
    assert q["a"]["planned_printer"] == "p1" and q["a"]["planned_start"] is not None
    assert out["unplaced"] == [{"material": "PLA", "color": "green", "jobs": ["g"], "minutes": 60}]
    blocked = build([_printer("p1")], [_job("a")], [], {"p1"}, now, W, TZ)
    assert blocked["lanes"][0]["why_not"] == "maintenance" and blocked["lanes"][0]["blocks"] == []


def test_queue_events_stay_out_of_the_coordinators_job_stream():
    from printfarm.contracts.messages import JobControlled

    assert not JobControlled.subject.startswith("evt.fleet.job.")      # the saga reads every evt.fleet.job.> as an order step

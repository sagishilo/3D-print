"""Phase 9 preparation, pure rules: printer models in fleet.toml (Bambu A1 + AMS Lite, A1 mini), the scheduler's
hardware fit (model a file was sliced for, multi-colour files to AMS printers only, build height, AMS colours), the
last check before a file goes to a printer, AMS slot mapping, the 3MF filament list and height, and model tasks."""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from printfarm.agents.fleet.maintenance import DEFAULT_TASKS, tasks_for
from printfarm.agents.fleet.policy import QJob, QPrinter, Window, forecast, no_printer, order_for, pick_plate, unfit
from printfarm.agents.personalization.catalog import gcode_max_z, inspect_3mf, slice_filaments
from printfarm.agents.telemetry.ams import AmsError, ams_mapping, distance, trays_from_report
from printfarm.agents.telemetry.platefiles import PlateFileError, check_fits
from printfarm.agents.telemetry.transfer import PlateFile
from printfarm.config import FleetCfg, FleetConfigError, printer_cfg
from printfarm.hardware import MODELS, sliced_for
from tests.test_catalog import NS, cube, three_mf

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Asia/Jerusalem")


def a1(**kw) -> dict:
    return {"id": "bambu-a1", "vendor": "bambu", "model": "a1", "nozzle_mm": 0.4, "material": "PLA", "color": "black",
            "ams_slots": 4, "ams_filaments": ["PLA/black", "PLA/white", "PLA/red", "PLA/blue"], **kw}


def mini(**kw) -> dict:
    return {"id": "bambu-a1-mini", "vendor": "bambu", "model": "a1-mini", "nozzle_mm": 0.4, "material": "PLA",
            "color": "black", **kw}


# ---------------------------------------------------------------- fleet.toml


def test_the_model_fills_bed_height_and_watts():
    big, small = printer_cfg(a1()), printer_cfg(mini())
    assert big.bed_mm == (256, 256) and big.max_z_mm == 256 and big.watts == 95
    assert small.bed_mm == (180, 180) and small.max_z_mm == 180 and small.watts == 80
    assert big.use_ams and big.ams_slots == 4 and not small.use_ams and small.ams_slots == 0
    assert big.loaded() == (("PLA", "black"), ("PLA", "white"), ("PLA", "red"), ("PLA", "blue"))
    assert small.loaded() == (("PLA", "black"),)


def test_what_fleet_toml_says_wins_over_the_model():
    p = printer_cfg(mini(bed_mm=[175, 175, 170], watts=72))
    assert p.bed_mm == (175, 175) and p.max_z_mm == 170 and p.watts == 72


@pytest.mark.parametrize(("bad", "says"), [
    (a1(ams_slots=8), "more than a Bambu Lab A1 can take"),
    (mini(use_ams=True), "use_ams = true needs ams_slots"),
    (a1(ams_filaments=["PLA black"]), "MATERIAL/color"),
    (a1(ams_slots=2), "4 ams_filaments for 2 AMS slots"),
    (mini(bed_mm=[180]), "bed_mm must be"),
    (mini(initial_print_hours=-1), "cannot be negative"),
    (mini(initial_print_hours=100, maintenance_done_hours={"lube-rods": 200}), "between 0 and initial_print_hours"),
])
def test_mistakes_in_fleet_toml_name_the_printer(bad, says):
    with pytest.raises(FleetConfigError, match=says):
        printer_cfg(bad)


def test_the_real_fleet_template_loads_and_describes_both_printers(tmp_path):
    """config/fleet.toml ships with the A1 + AMS Lite and A1 mini blocks commented out: uncommented they load."""
    text = (ROOT / "config/fleet.toml").read_text(encoding="utf-8")
    start = text.index("# >>> bambu-a1")
    end = text.index("# <<< bambu-a1-mini")
    block = "\n".join(line[2:] if line.startswith("# ") and not line.startswith("# >>>") and not line.startswith("# <<<")
                      else ("" if line.startswith("#") else line) for line in text[start:end].splitlines())
    f = tmp_path / "fleet.toml"
    f.write_text(text + "\n" + block, encoding="utf-8")
    assert tomllib.loads(f.read_text(encoding="utf-8"))
    with pytest.raises(FleetConfigError, match="printer bambu-a1: fill in host"):
        FleetCfg.load(f)
    f.write_text(text + "\n" + re.sub(r"<[^>]*>", "x", block), encoding="utf-8")
    fleet = FleetCfg.load(f)
    by = {p.id: p for p in fleet.printers}
    assert by["bambu-a1"].ams_slots == 4 and by["bambu-a1"].initial_print_hours == 1116
    assert by["bambu-a1-mini"].ams_slots == 0 and by["bambu-a1-mini"].initial_print_hours == 497
    assert by["bambu-a1-mini"].max_z_mm == 180 and by["bambu-a1"].max_z_mm == 256
    assert by["bambu-a1"].access_code_env == "BAMBU_CODE_BAMBU_A1"
    assert by["bambu-a1-mini"].access_code_env == "BAMBU_CODE_BAMBU_A1_MINI"


# ---------------------------------------------------------------- scheduler fit


def qp(cfg: dict, **kw) -> QPrinter:
    p = printer_cfg(cfg)
    return QPrinter(p.id, p.material, p.color, p.bed_mm, NOW, loaded=p.loaded() if p.ams_slots else (),
                    ams_slots=p.ams_slots, max_z=p.max_z_mm, model=p.model, **kw)


def job(jid: str, **kw) -> QJob:
    base = {"order_id": "o-" + jid, "material": "PLA", "color": "black", "w": 60, "d": 60, "est_minutes": 60,
            "est_grams": 20, "created_at": NOW}
    return QJob(jid, **{**base, **kw})


A1, MINI = qp(a1()), qp(mini())


def test_a_multi_colour_file_goes_only_to_the_printer_with_the_ams():
    j = job("mc", filament_count=3, whole_plate=True, printer_model="N2S")
    assert unfit(A1, j) is None
    assert unfit(MINI, j) == "sliced for Bambu Lab A1, not for this printer"
    any_model = job("mc2", filament_count=3, whole_plate=True)
    assert unfit(MINI, any_model) == "3 colours: needs a printer with an AMS"
    assert no_printer([MINI], any_model) == "3 colours: needs a printer with an AMS"
    assert no_printer([A1, MINI], any_model) is None


def test_a_file_sliced_for_the_mini_never_goes_to_the_a1():
    j = job("m", printer_model="N1", whole_plate=True)
    assert unfit(MINI, j) is None and "sliced for Bambu Lab A1 mini" in unfit(A1, j)
    assert sliced_for("a1", "N2S") and sliced_for("p1", "C12") is None and sliced_for("a1", None) is None


def test_too_tall_for_the_mini_goes_to_the_a1():
    j = job("tall", max_z_mm=200)
    assert unfit(MINI, j) == "200 mm tall: this printer prints up to 180 mm"
    assert unfit(A1, j) is None


def test_the_a1_takes_any_colour_in_its_ams_the_mini_only_its_spool():
    red = job("red", color="red")
    assert unfit(A1, red) is None
    assert unfit(MINI, red) == "needs PLA red loaded"
    green = job("green", color="green")
    assert no_printer([A1, MINI], green) == "no printer has PLA green loaded"


def test_an_ams_printer_still_prints_one_filament_per_plate():
    w = Window()
    ranked = order_for(A1, [job("b1"), job("r1", color="red"), job("b2")], NOW, w, TZ)
    assert {r.job.job_id for r in ranked} == {"b1", "r1", "b2"}       # every colour in its AMS is its work
    pick = pick_plate(A1, ranked, margin=5, spacing=4, max_minutes=480)
    assert pick.jobs == ("b1", "b2") and (pick.material, pick.color) == ("PLA", "black")


def test_the_forecast_sends_multi_colour_to_the_a1_and_small_work_to_both():
    jobs = [job("mc", filament_count=2, whole_plate=True, est_minutes=120),
            job("s1", w=150, d=150), job("s2", w=150, d=150)]
    fc = forecast([A1, MINI], jobs, NOW, Window(), TZ)
    a1_jobs = [j for b in fc.lanes["bambu-a1"] for j in b.jobs]
    mini_jobs = [j for b in fc.lanes["bambu-a1-mini"] for j in b.jobs]
    assert "mc" in a1_jobs and "mc" not in mini_jobs
    assert set(a1_jobs + mini_jobs) == {"mc", "s1", "s2"} and mini_jobs
    assert not fc.unplaced


def test_a_part_bigger_than_the_mini_bed_waits_for_the_a1():
    big = job("big", w=200, d=200)
    assert pick_plate(MINI, order_for(MINI, [big], NOW, Window(), TZ), margin=5, spacing=4, max_minutes=480) is None
    assert pick_plate(A1, order_for(A1, [big], NOW, Window(), TZ), margin=5, spacing=4, max_minutes=480).jobs == ("big",)


# ---------------------------------------------------------------- the last check before a printer


def plate(**kw) -> PlateFile:
    return PlateFile("plate-1", Path("x.gcode.3mf"), "plate-1.gcode.3mf", "md5", 1, **kw)


def test_the_printer_refuses_a_file_for_another_model_too_many_colours_or_too_tall():
    with pytest.raises(PlateFileError, match="sliced for Bambu Lab A1, this printer is Bambu Lab A1 mini"):
        check_fits(plate(printer_model="N2S"), printer_model="a1-mini", max_z=180)
    with pytest.raises(PlateFileError, match="uses 2 filaments and this printer has no AMS"):
        check_fits(plate(filaments=((1, "PLA", "#000000"), (2, "PLA", "#FFFFFF"))), printer_model="a1-mini")
    with pytest.raises(PlateFileError, match="200 mm tall; this printer prints up to 180 mm"):
        check_fits(plate(max_z_mm=200), printer_model="a1-mini", max_z=180)
    check_fits(plate(printer_model="N2S", filaments=((1, "PLA", "#000000"), (2, "PLA", "#FFFFFF")), max_z_mm=100),
               printer_model="a1", max_z=256, ams_slots=4)


# ---------------------------------------------------------------- AMS mapping

REPORT = {"ams": {"ams": [{"id": "0", "humidity": "4", "tray": [
    {"id": "0", "tray_type": "PLA", "tray_color": "000000FF"},
    {"id": "1", "tray_type": "PLA", "tray_color": "FFFFFFFF"},
    {"id": "2", "tray_type": "PETG", "tray_color": "C12E1FFF"},
    {"id": "3"},                                                     # empty slot
]}]}}


def test_trays_come_from_the_ams_report_and_empty_slots_are_left_out():
    assert trays_from_report(REPORT) == [(0, "PLA", "#000000"), (1, "PLA", "#FFFFFF"), (2, "PETG", "#C12E1F")]
    assert trays_from_report({}) == []


def test_each_filament_goes_to_the_slot_with_its_type_and_closest_colour():
    trays = trays_from_report(REPORT)
    # the file uses filaments 1 (white PLA) and 3 (red PETG); 2 is not on this plate
    assert ams_mapping([(1, "PLA", "#F8F8F8"), (3, "PETG", "#BE2D20")], trays) == [1, -1, 2]
    assert ams_mapping([(1, "PLA", "#000000")], trays) == [0]
    assert ams_mapping([], trays) == []
    assert distance("#000000", "#FFFFFF") > 400 and distance("", "#FFFFFF") == 0


def test_a_missing_material_or_a_clearly_different_colour_refuses_the_start():
    trays = trays_from_report(REPORT)
    with pytest.raises(AmsError, match="needs TPU"):
        ams_mapping([(1, "TPU", "#000000")], trays)
    with pytest.raises(AmsError, match="closest in the AMS is #C12E1F in slot 3"):
        ams_mapping([(1, "PETG", "#00FF00")], trays)
    with pytest.raises(AmsError, match="none free"):                 # two PETG filaments, one PETG slot
        ams_mapping([(1, "PETG", "#C12E1F"), (2, "PETG", "#C12E1F")], trays)
    with pytest.raises(AmsError, match="no loaded slots"):
        ams_mapping([(1, "PLA", "#000000")], [])


# ---------------------------------------------------------------- what the slicer says

MULTI = """<?xml version="1.0" encoding="UTF-8"?>
<config><plate><metadata key="index" value="1"/><metadata key="printer_model_id" value="N2S"/>
<metadata key="prediction" value="3600"/><metadata key="weight" value="40"/>
<filament id="1" type="PLA" color="#000000" used_g="30"/><filament id="3" type="PLA" color="#ffffff" used_g="10"/>
</plate></config>"""


def test_a_sliced_file_tells_its_filaments_model_and_height():
    data = three_mf({
        "3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 40, 20, 5)}</resources>'
                            '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 100 0"/></build></model>',
        "Metadata/plate_1.gcode": "; HEADER_BLOCK_START\n; max_z_height: 42.40\n; HEADER_BLOCK_END\nG28\n",
        "Metadata/slice_info.config": MULTI,
    })
    info = inspect_3mf(data)
    assert info.filaments == ((1, "PLA", "#000000"), (3, "PLA", "#FFFFFF")) and info.max_z_mm == 42.4
    assert info.printer_model == "N2S"
    assert slice_filaments(MULTI.encode()) == info.filaments
    assert gcode_max_z(b"G28\n") is None


# ---------------------------------------------------------------- maintenance per model


def test_a1_printers_get_their_own_maintenance_tasks():
    ids = {t["task_id"] for t in tasks_for("a1")}
    assert {"hotend-wear", "cutter-ptfe", "bed-cable"} <= ids and ids == {t["task_id"] for t in tasks_for("A1-mini")}
    assert tasks_for("klipper") == DEFAULT_TASKS
    assert set(MODELS) >= {"a1", "a1-mini"}

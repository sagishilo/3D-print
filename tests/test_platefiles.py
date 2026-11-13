"""Which file goes to the printer: sliced files pass through, placeholders only ever reach simulated printers."""

import hashlib
import zipfile

import pytest

from printfarm.agents.telemetry.platefiles import PlateFileError, local_path, resolve_plate_file


def make_3mf(path, plates=("Metadata/plate_1.gcode",)):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        for name in plates:
            z.writestr(name, "G1 X10 Y10\n")
    return path


def test_sliced_bambu_3mf_is_sent_as_is_under_the_plate_name(tmp_path):
    src = make_3mf(tmp_path / "riser.gcode.3mf", ("Metadata/plate_2.gcode",))
    pf = resolve_plate_file("plate-1", [src.as_uri()], vendor="bambu", simulated=False, out_dir=tmp_path)
    assert pf.path == src and pf.remote_name == "plate-1.gcode.3mf" and pf.plate_gcode == "Metadata/plate_2.gcode"
    assert pf.md5 == hashlib.md5(src.read_bytes()).hexdigest() and pf.size == src.stat().st_size


@pytest.mark.parametrize("plates,message", [((), "no sliced plate"),
                                            (("Metadata/plate_1.gcode", "Metadata/plate_2.gcode"), "2 sliced plates")])
def test_unusable_3mf_is_refused(tmp_path, plates, message):
    src = make_3mf(tmp_path / "project.3mf", plates)
    with pytest.raises(PlateFileError, match=message):
        resolve_plate_file("plate-1", [src.as_uri()], vendor="bambu", simulated=False, out_dir=tmp_path)


def test_corrupt_3mf_is_refused(tmp_path):
    src = tmp_path / "broken.3mf"
    src.write_bytes(b"not a zip")
    with pytest.raises(PlateFileError, match="not a valid 3MF"):
        resolve_plate_file("plate-1", [src.as_uri()], vendor="bambu", simulated=True, out_dir=tmp_path)


def test_sliced_gcode_for_klipper(tmp_path):
    src = tmp_path / "easel.gcode"
    src.write_text("G28\n")
    pf = resolve_plate_file("plate-7", [src.as_uri()], vendor="moonraker", simulated=False, out_dir=tmp_path)
    assert pf.remote_name == "plate-7.gcode" and pf.path == src


def test_missing_sliced_file_is_refused(tmp_path):
    with pytest.raises(PlateFileError, match="does not exist"):
        resolve_plate_file("plate-1", [(tmp_path / "gone.gcode").as_uri()], vendor="moonraker", simulated=False,
                           out_dir=tmp_path)


@pytest.mark.parametrize("vendor", ["bambu", "moonraker"])
def test_real_printer_never_gets_a_placeholder(tmp_path, vendor):
    parts = [(tmp_path / f"unit-{i}.stl").as_uri() for i in (1, 2, 3)]
    with pytest.raises(PlateFileError, match="no sliced"):
        resolve_plate_file("plate-1", parts, vendor=vendor, simulated=False, out_dir=tmp_path)
    assert not (tmp_path / "plates").exists()


def test_simulated_bambu_printer_gets_a_placeholder_3mf(tmp_path):
    parts = [(tmp_path / f"unit-{i}.stl").as_uri() for i in (1, 2)]
    pf = resolve_plate_file("plate-3", parts, vendor="bambu", simulated=True, out_dir=tmp_path)
    assert pf.remote_name == "plate-3.gcode.3mf" and pf.path.parent == tmp_path / "plates"
    with zipfile.ZipFile(pf.path) as z:
        gcode = z.read("Metadata/plate_1.gcode").decode()
        assert "NOT printable" in gcode and "unit-1.stl" in gcode and "unit-2.stl" in gcode
        assert z.read("Metadata/plate_1.gcode.md5").decode() == hashlib.md5(gcode.encode()).hexdigest()
    assert pf.md5 == hashlib.md5(pf.path.read_bytes()).hexdigest()


def test_simulated_klipper_printer_gets_a_placeholder_gcode(tmp_path):
    pf = resolve_plate_file("plate-4", [(tmp_path / "a.stl").as_uri()], vendor="moonraker", simulated=True, out_dir=tmp_path)
    assert pf.remote_name == "plate-4.gcode" and pf.path.read_text().startswith("; PrintFarm SIMULATOR placeholder")


def test_unknown_vendor_and_non_file_uris(tmp_path):
    with pytest.raises(PlateFileError, match="vendor"):
        resolve_plate_file("p", ["file:///x.gcode"], vendor="prusa", simulated=True, out_dir=tmp_path)
    with pytest.raises(PlateFileError, match="not a local file"):
        local_path("https://example.com/plate.3mf")
    assert local_path(tmp_path.as_uri()) == tmp_path

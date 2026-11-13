"""Which file goes to the printer for a plate.

Rules, in order:
1. The plate has exactly one artifact and it is already sliced for this kind of printer (Bambu: a ``.gcode.3mf`` /
   ``.3mf`` that contains ``Metadata/plate_N.gcode``; Klipper: ``.gcode``): send it, renamed after the plate.
2. The printer is a SIMULATED printer: build a small placeholder plate file that lists the parts, so the whole
   upload -> start -> verify path still runs.
3. Otherwise refuse: a real printer must never receive a placeholder. The plate fails with a reason the operator
   can act on (slice it, or use the plate compositor once it exists).
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from printfarm.agents.telemetry.transfer import PlateFile, md5_of

SLICED_SUFFIXES = {"bambu": (".gcode.3mf", ".3mf"), "moonraker": (".gcode",)}
_PLATE_GCODE = re.compile(r"^Metadata/plate_\d+\.gcode$")


class PlateFileError(RuntimeError):
    """No printable file for this plate; the message is shown to the operator."""


def local_path(uri: str) -> Path:
    u = urlparse(uri)
    if u.scheme not in ("file", ""):
        raise PlateFileError(f"artifact {uri} is not a local file")
    return Path(url2pathname(unquote(u.path))) if u.scheme == "file" else Path(uri)


def _sliced_suffix(path: Path, vendor: str) -> str | None:
    name = path.name.lower()
    return next((s for s in SLICED_SUFFIXES.get(vendor, ()) if name.endswith(s)), None)


def _bambu_plate_gcode(path: Path) -> str:
    """The sliced plate inside a 3MF (``Metadata/plate_N.gcode``); refuses unsliced project files."""
    try:
        with zipfile.ZipFile(path) as z:
            plates = sorted(n for n in z.namelist() if _PLATE_GCODE.match(n))
    except zipfile.BadZipFile as exc:
        raise PlateFileError(f"{path.name} is not a valid 3MF file") from exc
    if not plates:
        raise PlateFileError(f"{path.name} has no sliced plate (Metadata/plate_N.gcode): "
                             "export it from the slicer as a sliced plate file")
    if len(plates) > 1:
        raise PlateFileError(f"{path.name} contains {len(plates)} sliced plates; export one plate per file")
    return plates[0]


def _placeholder(plate_id: str, parts: list[Path], vendor: str, out_dir: Path) -> Path:
    lines = [f"; PrintFarm SIMULATOR placeholder for plate {plate_id} - NOT printable",
             *(f"; part {i}: {p.name}" for i, p in enumerate(parts, 1)), "M117 simulated plate", ""]
    gcode = "\n".join(lines).encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    if vendor == "bambu":
        path = out_dir / f"{plate_id}.gcode.3mf"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Metadata/plate_1.gcode", gcode)
            z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(gcode).hexdigest())  # noqa: S324 - like the slicer
        path.write_bytes(buf.getvalue())
    else:
        path = out_dir / f"{plate_id}.gcode"
        path.write_bytes(gcode)
    return path


def resolve_plate_file(plate_id: str, artifact_uris: list[str], *, vendor: str, simulated: bool, out_dir: Path) -> PlateFile:
    if vendor not in SLICED_SUFFIXES:
        raise PlateFileError(f"no plate format known for printer vendor {vendor!r}")
    parts = [local_path(u) for u in artifact_uris]
    if not parts:
        raise PlateFileError(f"plate {plate_id} has no parts")

    if len(parts) == 1 and (suffix := _sliced_suffix(parts[0], vendor)):
        path = parts[0]
        if not path.is_file():
            raise PlateFileError(f"sliced file {path} does not exist on this host")
        if vendor == "bambu":
            plate_gcode = _bambu_plate_gcode(path)
            return PlateFile(plate_id, path, f"{plate_id}.gcode.3mf", md5_of(path), path.stat().st_size, plate_gcode)
        return PlateFile(plate_id, path, f"{plate_id}{suffix}", md5_of(path), path.stat().st_size)

    if not simulated:
        sliced = SLICED_SUFFIXES[vendor][0]
        raise PlateFileError(
            f"plate {plate_id} has {len(parts)} part(s) and no sliced {sliced} file for this printer. "
            "Slice the plate (or one part per plate) first; automatic plate building comes with the plate compositor."
        )
    path = _placeholder(plate_id, parts, vendor, out_dir / "plates")
    return PlateFile(plate_id, path, path.name, md5_of(path), path.stat().st_size)

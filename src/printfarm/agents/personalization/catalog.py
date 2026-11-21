"""Instant Catalog rules, pure and unit-tested (tests/test_catalog.py): what a 3MF template contains, and what a batch
of SKUs costs in plates, hours and filament. Used by the personalization agent and by the gateway (no agent imports).

3MF inspection (``inspect_3mf``)
    A 3MF is a zip. ``3D/3dmodel.model`` holds the build items (objects, possibly in ``3D/Objects/*.model`` files,
    placed by 4x3 transforms). A sliced export (Bambu Studio / OrcaSlicer) adds ``Metadata/plate_N.gcode`` and
    ``Metadata/slice_info.config`` with the slicer's own prediction (seconds) and weight (grams) per plate.
    The footprint is the bed area the build items cover (bounding box of each object's transformed corners).
    Uploads are untrusted: size and entry limits, no DTDs, nothing extracted to disk.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from printfarm.agents.fleet.packing import JobSpec, build_plate

MAX_ENTRIES = 2000
MAX_UNCOMPRESSED = 400 * 1024 * 1024      # all entries together
MAX_RATIO = 200                           # compression ratio above which an entry is treated as a zip bomb
_PLATE_GCODE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


class InvalidThreeMf(ValueError):
    """The upload is not a usable 3MF; the message is shown to the operator."""


@dataclass(frozen=True)
class ThreeMfInfo:
    sliced: bool
    plates: int                               # sliced plates in the file (0 when unsliced)
    objects: int                              # build items
    footprint_mm: tuple[float, float] | None  # None when the model has no vertices we could read
    est_grams: float | None                   # from the slicer (sliced files only)
    est_minutes: float | None
    printer_model: str | None                 # the slicer's printer model id, when it says
    filament: str | None                      # filament type of the slice, e.g. "PLA"
    warnings: tuple[str, ...] = ()
    # every filament the sliced plate uses, (slicer filament id, type, "#RRGGBB"): more than one = an AMS print
    filaments: tuple[tuple[int, str, str], ...] = ()
    max_z_mm: float | None = None             # height of the sliced plate (G-code header), when the slicer says

    def as_dict(self) -> dict:
        return {"sliced": self.sliced, "plates": self.plates, "objects": self.objects,
                "footprint_mm": list(self.footprint_mm) if self.footprint_mm else None, "est_grams": self.est_grams,
                "est_minutes": self.est_minutes, "printer_model": self.printer_model, "filament": self.filament,
                "warnings": list(self.warnings), "filaments": [list(f) for f in self.filaments],
                "max_z_mm": self.max_z_mm}


def _xml(data: bytes, name: str) -> ET.Element:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise InvalidThreeMf(f"{name}: document type declarations are not allowed")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise InvalidThreeMf(f"{name} is not valid XML: {exc}") from None


def _matrix(text: str | None) -> tuple[float, ...]:
    if not text:
        return (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
    vals = tuple(float(v) for v in text.split())
    if len(vals) != 12:
        raise InvalidThreeMf(f"bad transform {text!r}")
    return vals


def _compose(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """The transform ``a`` then ``b`` (3MF row-vector convention: p' = p * M)."""
    ra = [a[0:3], a[3:6], a[6:9]]
    rb = [b[0:3], b[3:6], b[6:9]]
    rot = [[sum(ra[i][k] * rb[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = [sum(a[9 + k] * rb[k][j] for k in range(3)) + b[9 + j] for j in range(3)]
    return (*rot[0], *rot[1], *rot[2], *t)


def _apply(m: tuple[float, ...], x: float, y: float, z: float) -> tuple[float, float]:
    return (x * m[0] + y * m[3] + z * m[6] + m[9], x * m[1] + y * m[4] + z * m[7] + m[10])


Box = tuple[float, float, float, float, float, float]   # min x, y, z, max x, y, z


@dataclass
class _Model:
    boxes: dict[str, Box] = field(default_factory=dict)                                  # object id -> mesh bbox
    parts: dict[str, list[tuple[str, str, tuple[float, ...]]]] = field(default_factory=dict)  # id -> (path, id, m)
    build: list[tuple[str, tuple[float, ...]]] = field(default_factory=list)


def _parse_model(root: ET.Element, path: str) -> _Model:
    out = _Model()
    for obj in root.iter(f"{_CORE}object"):
        oid = obj.get("id", "")
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for v in obj.iter(f"{_CORE}vertex"):
            xs.append(float(v.get("x", 0)))
            ys.append(float(v.get("y", 0)))
            zs.append(float(v.get("z", 0)))
        if xs:
            out.boxes[oid] = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
        comps = [(c.get(f"{_PROD}path") or path, c.get("objectid", ""), _matrix(c.get("transform")))
                 for c in obj.iter(f"{_CORE}component")]
        if comps:
            out.parts[oid] = comps
    build = root.find(f"{_CORE}build")
    if build is not None:
        out.build = [(item.get("objectid", ""), _matrix(item.get("transform"))) for item in build.iter(f"{_CORE}item")]
    return out


def _corners(box: Box) -> Iterable[tuple[float, float, float]]:
    x0, y0, z0, x1, y1, z1 = box
    return ((x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1))


def _plate_groups(data: bytes) -> list[set[str]]:
    """The build objects on each plate of a Bambu Studio / OrcaSlicer project (``Metadata/model_settings.config``):
    one set of root object ids per plate that has any. A project lays its plates side by side on one build area,
    so the plates must be measured one at a time."""
    groups: list[set[str]] = []
    for plate in _xml(data, "model_settings.config").iter("plate"):
        ids = {m.get("value", "") for inst in plate.iter("model_instance")
               for m in inst.iter("metadata") if m.get("key") == "object_id"}
        ids.discard("")
        if ids:
            groups.append(ids)
    return groups


def _footprint(models: dict[str, _Model], root_path: str, only: set[str] | None = None) -> tuple[float, float] | None:
    """The bed area the build items cover; ``only``: just the build items of these root object ids (one plate)."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(path: str, oid: str, m: tuple[float, ...], depth: int) -> None:
        if depth > 8:
            raise InvalidThreeMf("components nest too deep")
        model = models.get(path)
        if model is None:
            return
        if oid in model.boxes:
            for c in _corners(model.boxes[oid]):
                x, y = _apply(m, *c)
                xs.append(x)
                ys.append(y)
        for sub_path, sub_id, sm in model.parts.get(oid, []):
            walk(sub_path, sub_id, _compose(sm, m), depth + 1)

    for oid, m in models[root_path].build:
        if only is None or oid in only:
            walk(root_path, oid, m, 0)
    if not xs:
        return None
    return (round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1))


def _slice_info(data: bytes) -> tuple[float | None, float | None, str | None, str | None]:
    """(grams, minutes, printer model, filament type) of plate 1 in ``Metadata/slice_info.config``."""
    root = _xml(data, "slice_info.config")
    plate = root.find("plate")
    if plate is None:
        return None, None, None, None
    meta = {m.get("key"): m.get("value") for m in plate.iter("metadata")}
    seconds = float(meta["prediction"]) if meta.get("prediction") else None
    grams = float(meta["weight"]) if meta.get("weight") else None
    fil = plate.find("filament")
    if grams is None and fil is not None and fil.get("used_g"):
        grams = float(fil.get("used_g"))
    return (round(grams, 1) if grams is not None else None, round(seconds / 60, 1) if seconds is not None else None,
            meta.get("printer_model_id") or None, fil.get("type") if fil is not None else None)


def slice_filaments(data: bytes) -> tuple[tuple[int, str, str], ...]:
    """The filaments plate 1 of ``Metadata/slice_info.config`` uses: (slicer filament id, type, "#RRGGBB")."""
    plate = _xml(data, "slice_info.config").find("plate")
    out = []
    for f in plate.iter("filament") if plate is not None else ():
        try:
            fid = int(f.get("id") or 0)
        except ValueError:
            continue
        color = (f.get("color") or "").strip().upper()
        out.append((fid, (f.get("type") or "").strip(), color[:7] if color.startswith("#") else ""))
    return tuple(sorted(out))


_MAX_Z = re.compile(rb"^;\s*max_z_height:\s*([0-9.]+)", re.M)


def gcode_max_z(head: bytes) -> float | None:
    """The plate's height from the slicer's G-code header (``; max_z_height: 12.40``), when it is there."""
    m = _MAX_Z.search(head)
    try:
        return round(float(m.group(1)), 2) if m else None
    except ValueError:
        return None


def inspect_3mf(data: bytes) -> ThreeMfInfo:
    """What an uploaded 3MF holds. Raises InvalidThreeMf for anything that is not a usable template."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InvalidThreeMf("not a 3MF file (a 3MF is a zip archive)") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise InvalidThreeMf(f"more than {MAX_ENTRIES} files inside")
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise InvalidThreeMf("unpacks to more than 400 MB")
        for i in infos:
            if i.file_size > 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                raise InvalidThreeMf(f"{i.filename} is compressed suspiciously well")
        names = {i.filename for i in infos}
        root_path = "3D/3dmodel.model"
        if root_path not in names:
            raise InvalidThreeMf("no 3D/3dmodel.model inside: export the project as 3MF")
        models = {
            n: _parse_model(_xml(z.read(n), n), "/" + n)
            for n in names if n.startswith("3D/") and n.endswith(".model")
        }
        models = {"/" + n: m for n, m in models.items()}
        root = models["/" + root_path]
        if not root.build:
            raise InvalidThreeMf("the 3MF has no objects on its build plate")
        plates = sorted(int(m.group(1)) for n in names if (m := _PLATE_GCODE.match(n)))
        warnings: list[str] = []
        footprint = None
        groups = (_plate_groups(z.read("Metadata/model_settings.config"))
                  if not plates and "Metadata/model_settings.config" in names else [])
        if len(groups) > 1:
            # an unsliced project with several plates: the largest single plate, never all plates together
            sizes = [fp for g in groups if (fp := _footprint(models, "/" + root_path, g))]
            if sizes:
                footprint = max(sizes, key=lambda s: (max(s), s[0] * s[1]))
            warnings.append(f"{len(groups)} plates in the project: the size is the largest plate's. "
                            "Slice one plate per file for the printers")
        elif "Metadata/plate_1.json" in names:
            try:
                bbox = json.loads(z.read("Metadata/plate_1.json")).get("bbox_all")
                if isinstance(bbox, list) and len(bbox) == 4:
                    footprint = (round(bbox[2] - bbox[0], 1), round(bbox[3] - bbox[1], 1))
            except (ValueError, TypeError):
                warnings.append("Metadata/plate_1.json unreadable")
        if footprint is None:
            footprint = _footprint(models, "/" + root_path)
        if footprint is not None and (footprint[0] <= 0 or footprint[1] <= 0 or not all(map(math.isfinite, footprint))):
            footprint = None
        grams = minutes = printer_model = filament = None
        filaments: tuple[tuple[int, str, str], ...] = ()
        max_z = None
        if plates and "Metadata/slice_info.config" in names:
            info = z.read("Metadata/slice_info.config")
            grams, minutes, printer_model, filament = _slice_info(info)
            filaments = slice_filaments(info)
        if plates:
            with z.open(f"Metadata/plate_{plates[0]}.gcode") as g:
                max_z = gcode_max_z(g.read(64 * 1024))
        if plates and (grams is None or minutes is None):
            warnings.append("sliced, but the slicer's time and weight were not found")
        if len(plates) > 1:
            warnings.append(f"{len(plates)} sliced plates: only one plate per file can be sent to a printer")
        return ThreeMfInfo(sliced=bool(plates), plates=len(plates), objects=len(root.build), footprint_mm=footprint,
                           est_grams=grams, est_minutes=minutes, printer_model=printer_model, filament=filament,
                           warnings=tuple(warnings), filaments=filaments, max_z_mm=max_z)


# ---------------------------------------------------------------- batches


@dataclass(frozen=True)
class BatchItem:
    sku: str
    quantity: int
    material: str
    color: str
    footprint_mm: tuple[float, float]
    est_grams: float
    est_minutes: float
    sliced: bool                               # a sliced template is printed one piece per plate


@dataclass(frozen=True)
class Shortage:
    material: str
    color: str
    need_grams: float
    available_grams: float

    @property
    def short_grams(self) -> float:
        return round(max(0.0, self.need_grams - self.available_grams), 1)


@dataclass(frozen=True)
class BatchEstimate:
    skus: int
    pieces: int
    plates: int
    minutes: float
    grams: float
    materials: tuple[Shortage, ...]            # every filament the batch uses, with what is free for it
    too_big: tuple[str, ...]                   # SKUs that do not fit the bed at all

    @property
    def short(self) -> tuple[Shortage, ...]:
        return tuple(m for m in self.materials if m.short_grams > 0)


def estimate_batch(items: list[BatchItem], available: dict[tuple[str, str], float], *, bed: tuple[float, float],
                   margin: float = 5, spacing: float = 4, max_minutes: float = 480) -> BatchEstimate:
    """Plates, print time and filament of a batch, packed the way the fleet scheduler packs (one material and color
    per plate; sliced templates alone on their plate), against the filament free for it (``available``: grams on
    spools minus what scheduled plates reserved and queued jobs will use)."""
    plates, minutes, too_big = 0, 0.0, []
    by_filament: dict[tuple[str, str], list[BatchItem]] = {}
    for it in items:
        if it.quantity > 0:
            by_filament.setdefault((it.material, it.color), []).append(it)
    for group in by_filament.values():
        jobs: list[JobSpec] = []
        for it in group:
            if build_plate([JobSpec("probe", *it.footprint_mm, it.est_minutes, it.est_grams)], bed, margin=margin,
                           spacing=spacing, max_minutes=max(max_minutes, it.est_minutes)) is None:
                too_big.append(it.sku)
                continue
            if it.sliced:
                plates += it.quantity
                minutes += it.est_minutes * it.quantity
            else:
                jobs += [JobSpec(f"{it.sku}#{n}", *it.footprint_mm, it.est_minutes, it.est_grams)
                         for n in range(it.quantity)]
        while jobs:
            plan = build_plate(jobs, bed, margin=margin, spacing=spacing,
                               max_minutes=max(max_minutes, max(j.est_minutes for j in jobs)))
            if plan is None:     # cannot happen after the probe above; never loop forever
                break
            placed = {p.item_id for p in plan.placements}
            jobs = [j for j in jobs if j.job_id not in placed]
            plates += 1
            minutes += plan.est_minutes
    need: dict[tuple[str, str], float] = {}
    for it in items:
        if it.quantity > 0 and it.sku not in too_big:
            need[(it.material, it.color)] = need.get((it.material, it.color), 0.0) + it.est_grams * it.quantity
    materials = tuple(Shortage(m, c, round(g, 1), round(available.get((m, c), 0.0), 1))
                      for (m, c), g in sorted(need.items()))
    return BatchEstimate(skus=len({it.sku for it in items if it.quantity > 0}),
                         pieces=sum(it.quantity for it in items if it.quantity > 0), plates=plates,
                         minutes=round(minutes, 1), grams=round(sum(need.values()), 1), materials=materials,
                         too_big=tuple(sorted(set(too_big))))

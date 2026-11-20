"""What each printer model can do (pure): build volume, average power, AMS support and the id its slicer writes into
a sliced 3MF (``Metadata/slice_info.config`` > ``printer_model_id``). fleet.toml names a model (``model = "a1"``)
and leaves out what the model already says; a value written in fleet.toml always wins.

Slicer ids are the ones Bambu Studio / OrcaSlicer write (community-documented): a file sliced for one model carries
that model's start G-code and bed size, so it must never be sent to another model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    key: str
    label: str
    bed_mm: tuple[float, float, float]     # width, depth, height of the build volume
    watts: float                           # average power while printing PLA (electricity cost per plate)
    slicer_ids: tuple[str, ...] = ()       # printer_model_id values of files sliced for this model
    ams_max_slots: int = 0                 # 0: no AMS can be attached


# Average watts: Bambu's own figures where published (A1 mini ~80 W on PLA, wiki "power consumption"); the others are
# typical PLA averages. Measure yours with a smart plug and write `watts` in fleet.toml for exact electricity costs.
MODELS: dict[str, Model] = {m.key: m for m in (
    Model("a1", "Bambu Lab A1", (256, 256, 256), 95, ("N2S",), 4),
    Model("a1-mini", "Bambu Lab A1 mini", (180, 180, 180), 80, ("N1",), 4),
    Model("p1p", "Bambu Lab P1P", (256, 256, 256), 110, ("C11",), 16),
    Model("p1s", "Bambu Lab P1S", (256, 256, 256), 110, ("C12",), 16),
    Model("x1c", "Bambu Lab X1 Carbon", (256, 256, 256), 120, ("BL-P001",), 16),
    Model("x1e", "Bambu Lab X1E", (256, 256, 256), 130, ("C13",), 16),
)}
# Older fleet files name a family, not a model: no slicer check, no defaults beyond what is written.
FAMILIES = {"a1", "p1", "x1", "klipper", ""}


def model(key: str) -> Model | None:
    return MODELS.get((key or "").strip().lower())


def slicer_ids(key: str) -> tuple[str, ...]:
    m = model(key)
    return m.slicer_ids if m else ()


def sliced_for(printer_model: str, file_model_id: str | None) -> bool | None:
    """Whether a file sliced for ``file_model_id`` may print on a ``printer_model`` printer: True / False, or None
    when either side is unknown (an old fleet file, an unsliced part)."""
    ids = slicer_ids(printer_model)
    if not ids or not file_model_id:
        return None
    return file_model_id in ids


def model_of_slicer_id(file_model_id: str | None) -> Model | None:
    return next((m for m in MODELS.values() if file_model_id and file_model_id in m.slicer_ids), None)

"""AMS slot mapping for a Bambu print (pure, tests/test_hardware.py).

A sliced 3MF lists the filaments its plate uses (``slice_info.config``: slicer filament id, type, colour). To start it
on a printer with an AMS (A1 + AMS Lite: one unit, slots 0-3), the ``project_file`` command carries ``ams_mapping``:
for each slicer filament id (1-based, position id-1) the AMS slot that feeds it (unit * 4 + slot), -1 for ids the
plate does not use (community-documented, OpenBambuAPI / ha-bambulab).

We map from what the AMS reports right now: the same filament type, the closest colour, each slot used once. A
filament with no matching slot, or only a clearly different colour, refuses the start with a reason the operator can
act on ("load PETG #FF0000 into the AMS"), instead of printing the wrong material.
"""

from __future__ import annotations

from collections.abc import Sequence

from printfarm.agents.telemetry.platefiles import PlateFileError

# RGB distance (0..441) above which two colours are "a different colour", not a shade of the same one
MAX_COLOR_DISTANCE = 90.0


class AmsError(PlateFileError):
    """The AMS cannot feed this plate as loaded; the message is shown to the operator."""


Tray = tuple[int, str, str]          # (AMS slot index: unit * 4 + slot, filament type, "#RRGGBB")
Filament = tuple[int, str, str]      # (slicer filament id, type, "#RRGGBB")


def trays_from_report(raw: dict) -> list[Tray]:
    """The loaded AMS slots of a Bambu ``print`` report (``ams.ams[].tray[]``); empty slots are left out."""
    out: list[Tray] = []
    for unit in (raw.get("ams") or {}).get("ams") or []:
        try:
            u = int(unit.get("id") or 0)
        except (TypeError, ValueError):
            continue
        for t in unit.get("tray") or []:
            kind = str(t.get("tray_type") or "").strip()
            if not kind:
                continue
            try:
                slot = int(t.get("id") or 0)
            except (TypeError, ValueError):
                continue
            color = str(t.get("tray_color") or "")
            out.append((u * 4 + slot, kind, f"#{color[:6].upper()}" if len(color) >= 6 else ""))
    return out


def _rgb(hex_: str) -> tuple[int, int, int] | None:
    h = hex_.lstrip("#")
    if len(h) < 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def distance(a: str, b: str) -> float:
    """RGB distance of two "#RRGGBB" colours; 0 when either is unknown (the type alone decides)."""
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return 0.0
    return sum((x - y) ** 2 for x, y in zip(ra, rb, strict=True)) ** 0.5


def ams_mapping(filaments: Sequence[Filament], trays: Sequence[Tray]) -> list[int]:
    """The ``ams_mapping`` for a plate that uses ``filaments`` on an AMS loaded with ``trays``."""
    if not filaments:
        return []
    if not trays:
        raise AmsError("the AMS reports no loaded slots: load the filament (or check the AMS is connected)")
    free = list(trays)
    chosen: dict[int, int] = {}
    # the most constrained filaments first: those with the fewest slots of their type
    for fid, kind, color in sorted(filaments, key=lambda f: sum(t[1].upper() == f[1].upper() for t in trays)):
        same = [t for t in free if t[1].upper() == kind.upper()]
        if not same:
            loaded = ", ".join(f"slot {t[0] + 1}: {t[1]} {t[2]}".strip() for t in trays)
            raise AmsError(f"the plate needs {kind} {color} and the AMS has none free ({loaded})".replace("  ", " "))
        best = min(same, key=lambda t: distance(color, t[2]))
        if distance(color, best[2]) > MAX_COLOR_DISTANCE:
            raise AmsError(f"the plate needs {kind} {color}; the closest in the AMS is {best[2]} in slot {best[0] + 1}: "
                           "load the right colour or slice with the AMS colours")
        chosen[fid] = best[0]
        free.remove(best)
    return [chosen.get(i, -1) for i in range(1, max(chosen) + 1)]

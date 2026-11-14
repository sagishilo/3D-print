"""Headless slicing of a catalog template (Slice now), when a slicer CLI is installed on the agent's host.

Supported: OrcaSlicer or Bambu Studio command line (``--slice 0 --export-3mf``), found on PATH or at ``SLICER_PATH``;
printer, process and filament profiles from ``SLICER_PROFILES`` (``machine.json;process.json`` and ``filament.json``
in that folder) when set, else the profiles stored in the template. The result must contain a sliced plate,
otherwise it counts as a failure.

Status: a spike. The agent image ships no slicer (the AppImage needs a desktop runtime), so Slice now says so
and the operator uploads a 3MF sliced on the PC instead. Only the failure paths are tested (tests/test_catalog.py).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from printfarm.agents.personalization.catalog import ThreeMfInfo, inspect_3mf

CANDIDATES = ("orca-slicer", "OrcaSlicer", "bambu-studio", "BambuStudio")


class SliceError(RuntimeError):
    """Slicing did not produce a printable plate; the message is shown to the operator."""


def find_slicer() -> str | None:
    env = os.environ.get("SLICER_PATH", "").strip()
    if env:
        return env if Path(env).is_file() else None
    return next((p for c in CANDIDATES if (p := shutil.which(c))), None)


def _profile_args() -> list[str]:
    folder = os.environ.get("SLICER_PROFILES", "").strip()
    if not folder:
        return []
    d = Path(folder)
    args = []
    if (d / "machine.json").is_file() and (d / "process.json").is_file():
        args += ["--load-settings", f"{d / 'machine.json'};{d / 'process.json'}"]
    if (d / "filament.json").is_file():
        args += ["--load-filaments", str(d / "filament.json")]
    return args


async def slice_3mf(slicer: str, src: Path, out: Path, timeout: float = 900) -> ThreeMfInfo:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [slicer, *_profile_args(), "--slice", "0", "--outputdir", str(out.parent), "--export-3mf", out.name, str(src)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise SliceError(f"the slicer took longer than {timeout:.0f} s") from None
    if proc.returncode != 0:
        raise SliceError(f"the slicer failed: {err.decode(errors='replace')[-400:]}")
    if not out.is_file():
        raise SliceError("the slicer finished without writing a file")
    info = inspect_3mf(out.read_bytes())
    if not info.sliced:
        raise SliceError("the slicer's file has no sliced plate")
    return info

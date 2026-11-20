"""Camera failure detection (Wave 2 / W2-C): a pluggable model behind a pure rule.

Every FRAME_EVERY_S the telemetry agent takes one frame from each printing printer's camera (go2rtc) and asks the
model how likely the print is failing (0..1: spaghetti, a detached part, a blob). ``Watch`` raises a suspicion only
after ``frames`` suspicious frames in a row, then stays quiet for COOLDOWN_S on that printer, so one bad frame never
interrupts a person. What happens next is a setting: by default a person is asked (Today, notification) and decides;
``vision.auto_pause`` also pauses the printer (verified, like every pause).

Models:
- ``off``   : no model (the default until one is chosen).
- ``fake``  : test and demo model; scores come from VISION_FAKE_SCORES="bambu-01=0.9,voron-01=0.1" (no frame needed).
- ``http``  : a model on this network that takes a JPEG and answers {"score": 0..1}, at VISION_MODEL_URL (LAN only;
              for example a local failure-detection container). Which model to run is the owner's choice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

FRAME_EVERY_S = 20.0
COOLDOWN_S = 15 * 60.0


@dataclass
class Watch:
    """Consecutive suspicious frames per printer (pure: the caller passes the time)."""

    threshold: float = 0.7
    frames: int = 3
    cooldown_s: float = COOLDOWN_S
    _run: dict[str, int] = field(default_factory=dict)
    _quiet_until: dict[str, float] = field(default_factory=dict)

    def feed(self, printer_id: str, score: float, now: float) -> bool:
        """One frame's score; True when this frame completes a run and a suspicion should be raised."""
        if score < self.threshold:
            self._run[printer_id] = 0
            return False
        self._run[printer_id] = self._run.get(printer_id, 0) + 1
        if self._run[printer_id] < self.frames or now < self._quiet_until.get(printer_id, 0.0):
            return False
        self._run[printer_id] = 0
        self._quiet_until[printer_id] = now + self.cooldown_s
        return True

    def reset(self, printer_id: str) -> None:
        """The printer stopped printing (or a new plate started): start counting again."""
        self._run.pop(printer_id, None)

    def run(self, printer_id: str) -> int:
        return self._run.get(printer_id, 0)


class Scorer(Protocol):
    name: str
    needs_frame: bool

    async def score(self, printer_id: str, jpeg: bytes | None) -> float | None: ...


class FakeScorer:
    name = "fake"
    needs_frame = False

    def __init__(self, scores: dict[str, float] | None = None):
        self.scores = dict(scores or {})

    @classmethod
    def from_env(cls, raw: str | None = None) -> FakeScorer:
        raw = os.getenv("VISION_FAKE_SCORES", "") if raw is None else raw
        scores: dict[str, float] = {}
        for part in filter(None, (p.strip() for p in raw.split(","))):
            pid, _, val = part.partition("=")
            try:
                scores[pid.strip()] = min(1.0, max(0.0, float(val)))
            except ValueError:
                continue
        return cls(scores)

    async def score(self, printer_id: str, jpeg: bytes | None) -> float | None:
        return self.scores.get(printer_id, 0.0)


class HttpScorer:
    """POST the JPEG to a model on the LAN; it answers {"score": 0..1}. Any error: no score for this frame."""

    name = "http"
    needs_frame = True

    def __init__(self, url: str, client):
        self.url, self.client = url, client

    async def score(self, printer_id: str, jpeg: bytes | None) -> float | None:
        if not jpeg:
            return None
        try:
            r = await self.client.post(self.url, content=jpeg, headers={"Content-Type": "image/jpeg"},
                                       params={"printer": printer_id}, timeout=10)
            r.raise_for_status()
            return min(1.0, max(0.0, float(r.json()["score"])))
        except Exception:  # noqa: BLE001 - a model hiccup is a missing frame, never a crash
            return None


def model_name() -> str:
    return os.getenv("VISION_MODEL", "off").strip().lower() or "off"

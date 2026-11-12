"""Rule-based failure detectors over the canonical PrinterState stream.

Each detector is a small stateful object per printer: ``feed(state)``
returns a ``Finding`` or ``None``. They are pure (no I/O) and run inside the
telemetry agent's hot path, so a CRITICAL finding pauses the printer within
one telemetry sample, without any LLM or network round trip.

Camera-based "spaghetti" detection plugs in as one more detector fed by
frame scores instead of temperatures (see 02-agent-catalog.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from printfarm.contracts.messages import PrinterState, PrinterStatus, Severity


@dataclass(frozen=True)
class Finding:
    detector: str
    severity: Severity
    message: str


class Detector:
    name = "detector"

    def feed(self, s: PrinterState) -> Finding | None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class ThermalRunaway(Detector):
    """Heater far from target for too long after it had reached the target once."""

    name = "thermal_runaway"
    heater: str = "nozzle"           # "nozzle" | "bed"
    max_deviation_c: float = 15.0
    hold_s: float = 10.0
    _stable: bool = False
    _since: datetime | None = None
    _target: float | None = None

    def feed(self, s: PrinterState) -> Finding | None:
        temp = getattr(s, f"{self.heater}_temp")
        target = getattr(s, f"{self.heater}_target")
        if temp is None or not target or target != self._target:
            # No target, or a new target: wait until the heater settles again.
            self._stable, self._since, self._target = False, None, target
            return None
        dev = abs(temp - target)
        if not self._stable:
            self._stable = dev < 3.0
            return None
        if dev <= self.max_deviation_c:
            self._since = None
            return None
        self._since = self._since or s.observed_at
        if (s.observed_at - self._since).total_seconds() >= self.hold_s:
            self._since = None
            return Finding(
                f"{self.name}_{self.heater}", Severity.CRITICAL,
                f"{self.heater} at {temp:.1f}C vs target {target:.0f}C for >= {self.hold_s:.0f}s",
            )
        return None


@dataclass
class HeatingFailure(Detector):
    """Target set but the heater is not making progress towards it."""

    name = "heating_failure"
    heater: str = "nozzle"
    window_s: float = 90.0
    min_rise_c: float = 3.0
    _start: tuple[datetime, float] | None = None

    def feed(self, s: PrinterState) -> Finding | None:
        temp = getattr(s, f"{self.heater}_temp")
        target = getattr(s, f"{self.heater}_target")
        if temp is None or not target or temp >= target - 10:
            self._start = None
            return None
        if self._start is None:
            self._start = (s.observed_at, temp)
            return None
        t0, temp0 = self._start
        if (s.observed_at - t0).total_seconds() >= self.window_s:
            self._start = None
            if temp - temp0 < self.min_rise_c:
                return Finding(f"{self.name}_{self.heater}", Severity.CRITICAL,
                               f"{self.heater} rose only {temp - temp0:.1f}C in {self.window_s:.0f}s")
        return None


@dataclass
class ProgressStall(Detector):
    """Printing but progress has not moved for a long time (clog, stuck job)."""

    name = "progress_stall"
    stall_s: float = 600.0
    _last: tuple[datetime, float] | None = None
    _fired: bool = False

    def feed(self, s: PrinterState) -> Finding | None:
        if s.status != PrinterStatus.PRINTING:
            self._last, self._fired = None, False
            return None
        if self._last is None or s.progress > self._last[1]:
            self._last, self._fired = (s.observed_at, s.progress), False
            return None
        if not self._fired and (s.observed_at - self._last[0]).total_seconds() >= self.stall_s:
            self._fired = True
            return Finding(self.name, Severity.WARNING, f"no progress for {self.stall_s / 60:.0f} min")
        return None


@dataclass
class PrinterReportedError(Detector):
    name = "printer_error"
    _last_code: str | None = None

    def feed(self, s: PrinterState) -> Finding | None:
        code = s.error_code if s.error_code not in (None, "", "0") else None
        if code and code != self._last_code:
            self._last_code = code
            return Finding(self.name, Severity.WARNING, f"printer reported error {code}")
        self._last_code = code
        return None


@dataclass
class DetectorSet:
    detectors: list[Detector] = field(
        default_factory=lambda: [
            ThermalRunaway(heater="nozzle"),
            ThermalRunaway(heater="bed", max_deviation_c=10.0, hold_s=30.0),
            HeatingFailure(heater="nozzle"),
            ProgressStall(),
            PrinterReportedError(),
        ]
    )

    def feed(self, s: PrinterState) -> list[Finding]:
        return [f for d in self.detectors if (f := d.feed(s))]

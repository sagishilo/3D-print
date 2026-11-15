"""Prints that ended while nobody was watching.

A print session opens when the printer reports a plate printing and closes when it reports that plate finished or
failed. If the agent (or this PC, or the network) was down at that moment, or the printer was restarted, or someone
cancelled at the printer's screen, the closing report never comes and fleet keeps the printer busy forever.

``LostPrintWatch`` notices: an open session whose printer, online, has reported for ``after_s`` seconds that it is
not running that plate (idle, finished/failed without it, or printing another plate). The plate is then closed as
failed with the bed state unknown: its jobs are reprinted, and the bed waits for an operator unless the fleet is
set to clear beds by itself. Offline and calibrating reports prove nothing and are ignored.
"""
from __future__ import annotations

from printfarm.contracts.messages import PrinterState, PrinterStatus

LOST_AFTER_S = 20.0
ACTIVE = {PrinterStatus.PRINTING, PrinterStatus.PAUSED}
DONE = {PrinterStatus.FINISHED, PrinterStatus.FAILED}
NO_EVIDENCE = {PrinterStatus.OFFLINE, PrinterStatus.CALIBRATING}


def contradicts(plate_id: str, s: PrinterState) -> bool:
    """True if this report shows the printer is not running ``plate_id`` (and will not report its end)."""
    if s.status in NO_EVIDENCE:
        return False
    return not (s.plate_id == plate_id and s.status in ACTIVE | DONE)


class LostPrintWatch:
    def __init__(self, after_s: float = LOST_AFTER_S):
        self.after_s = after_s
        self.open: dict[str, set[str]] = {}
        self.since: dict[tuple[str, str], float] = {}

    def opened(self, printer_id: str, plate_id: str) -> None:
        self.open.setdefault(printer_id, set()).add(plate_id)

    def closed(self, printer_id: str, plate_id: str) -> None:
        self.open.get(printer_id, set()).discard(plate_id)
        self.since.pop((printer_id, plate_id), None)

    def check(self, printer_id: str, s: PrinterState, now: float) -> list[str]:
        """The plates to close as lost now (each once: they leave the watch)."""
        lost = []
        for plate in sorted(self.open.get(printer_id, set())):
            key = (printer_id, plate)
            if not contradicts(plate, s):
                if s.status not in NO_EVIDENCE:
                    self.since.pop(key, None)        # the printer confirms it again: forget the suspicion
                continue
            first = self.since.setdefault(key, now)
            if now - first >= self.after_s:
                lost.append(plate)
        for plate in lost:
            self.closed(printer_id, plate)
        return lost

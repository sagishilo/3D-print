"""Per-printer power draw: fleet.toml ``watts`` reaches each plate's electricity cost (finance default otherwise)."""

from __future__ import annotations

from types import SimpleNamespace

from printfarm.agents.fleet.agent import FleetAgent
from printfarm.config import PrinterCfg
from printfarm.contracts.messages import PlateFinished


def printer(pid: str, watts: float = 0) -> PrinterCfg:
    return PrinterCfg(id=pid, vendor="bambu", bed_mm=(256, 256), nozzle_mm=0.4, material="PLA", color="black", watts=watts)


def test_configured_watts_are_sent_with_the_plate_and_zero_means_the_default():
    agent = SimpleNamespace(fleet=SimpleNamespace(printers=[printer("x1c-02", 110), printer("p1s-01")]))
    assert FleetAgent._watts(agent, "x1c-02") == 110
    assert FleetAgent._watts(agent, "p1s-01") is None
    assert FleetAgent._watts(agent, "gone") is None


def test_older_plate_messages_without_watts_still_parse():
    ev = PlateFinished.model_validate({"plate_id": "p", "printer_id": "x", "material": "PLA", "color": "black",
                                       "succeeded": True, "grams_used": 12.5})
    assert ev.printer_watts is None
    assert PlateFinished(plate_id="p", printer_id="x", material="PLA", color="black", succeeded=True, grams_used=1,
                         printer_watts=95.5).printer_watts == 95.5

from datetime import UTC, datetime, timedelta

from printfarm.agents.telemetry.detectors import DetectorSet, HeatingFailure, ProgressStall, ThermalRunaway
from printfarm.contracts.messages import PrinterState, PrinterStatus, Severity

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def st(sec, nozzle=220.0, target=220.0, progress=0.5, status=PrinterStatus.PRINTING, **kw):
    return PrinterState(printer_id="p", vendor="sim", status=status, progress=progress, nozzle_temp=nozzle,
                        nozzle_target=target, bed_temp=60, bed_target=60,
                        observed_at=T0 + timedelta(seconds=sec), **kw)


def test_thermal_runaway_fires_after_hold():
    d = ThermalRunaway()
    assert d.feed(st(0)) is None          # new target -> arming
    assert d.feed(st(1)) is None          # settled -> stable
    assert d.feed(st(2, nozzle=240)) is None
    assert d.feed(st(8, nozzle=245)) is None
    f = d.feed(st(12, nozzle=250))
    assert f and f.severity == Severity.CRITICAL


def test_thermal_runaway_ignores_heat_up_and_target_change():
    d = ThermalRunaway()
    for i, temp in enumerate([30, 80, 150, 200, 219, 220]):
        assert d.feed(st(i * 5, nozzle=temp)) is None
    # new target (e.g. 250 for PETG): heating towards it is not a runaway
    for i, temp in enumerate([222, 230, 240, 248]):
        assert d.feed(st(100 + i * 10, nozzle=temp, target=250)) is None


def test_short_spike_is_tolerated():
    d = ThermalRunaway()
    d.feed(st(0)), d.feed(st(1))
    assert d.feed(st(2, nozzle=240)) is None
    assert d.feed(st(5, nozzle=221)) is None
    assert d.feed(st(20, nozzle=240)) is None


def test_heating_failure():
    d = HeatingFailure(window_s=60)
    assert d.feed(st(0, nozzle=30)) is None
    f = d.feed(st(61, nozzle=31))
    assert f and f.severity == Severity.CRITICAL


def test_progress_stall_fires_once():
    d = ProgressStall(stall_s=600)
    assert d.feed(st(0, progress=0.4)) is None
    assert d.feed(st(601, progress=0.4)).severity == Severity.WARNING
    assert d.feed(st(900, progress=0.4)) is None


def test_healthy_print_produces_no_findings():
    ds = DetectorSet()
    findings = []
    for i in range(200):
        findings += ds.feed(st(i, nozzle=220 + (i % 3) - 1, progress=i / 200))
    assert findings == []

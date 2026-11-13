"""Pause verification timing, with a fake clock and fake printers (no network)."""

from datetime import UTC, datetime

from printfarm.agents.telemetry.safety import pause_and_verify
from printfarm.contracts.messages import PrinterState, PrinterStatus


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, dt: float) -> None:
        self.t += dt


class FakePrinter:
    """Pauses ``delay_s`` after the ``obey_on``-th pause command (None = never); can be unreachable."""

    def __init__(self, clock: Clock, *, delay_s: float = 0.5, obey_on: int | None = 1, reachable: bool = True,
                 stops_as: PrinterStatus = PrinterStatus.PAUSED):
        self.clock, self.delay_s, self.obey_on, self.reachable, self.stops_as = clock, delay_s, obey_on, reachable, stops_as
        self.calls = 0
        self.stopped_at: float | None = None

    async def pause(self) -> None:
        self.calls += 1
        if not self.reachable:
            raise ConnectionError("printer not connected")
        if self.obey_on is not None and self.calls >= self.obey_on and self.stopped_at is None:
            self.stopped_at = self.clock.t + self.delay_s

    def state(self) -> PrinterState:
        stopped = self.stopped_at is not None and self.clock.t >= self.stopped_at
        return PrinterState(printer_id="p1", vendor="bambu", plate_id="plate-1",
                            status=self.stops_as if stopped else PrinterStatus.PRINTING, observed_at=datetime.now(UTC))


async def verify(printer: FakePrinter, clock: Clock, confirm_s=10, retries=1):
    return await pause_and_verify(send_pause=printer.pause, current_state=printer.state, is_online=lambda: printer.reachable,
                                  confirm_s=confirm_s, retries=retries, poll_s=0.1, clock=clock.now, sleep=clock.sleep)


async def test_pause_confirmed_with_latency():
    clock = Clock()
    out = await verify(FakePrinter(clock, delay_s=1.8), clock)
    assert out.confirmed and out.attempts == 1 and out.status == PrinterStatus.PAUSED
    assert 1.8 <= out.elapsed_s < 2.0


async def test_lost_pause_is_retried_then_confirmed():
    clock = Clock()
    printer = FakePrinter(clock, obey_on=2, delay_s=0.3)  # first command lost
    out = await verify(printer, clock, confirm_s=5)
    assert out.confirmed and out.attempts == 2 and printer.calls == 2
    assert 5.3 <= out.elapsed_s < 5.5


async def test_ignored_pause_is_unconfirmed_after_all_attempts():
    clock = Clock()
    printer = FakePrinter(clock, obey_on=None)  # firmware drops third-party control
    out = await verify(printer, clock, confirm_s=10, retries=1)
    assert not out.confirmed and out.attempts == 2 and printer.calls == 2
    assert out.status == PrinterStatus.PRINTING and out.online
    assert 20.0 <= out.elapsed_s < 20.3


async def test_unreachable_printer_is_unconfirmed_with_errors():
    clock = Clock()
    out = await verify(FakePrinter(clock, reachable=False), clock, confirm_s=2, retries=2)
    assert not out.confirmed and out.attempts == 3 and not out.online
    assert len(out.send_errors) == 3 and "not connected" in out.send_errors[0]


async def test_printer_that_stops_another_way_counts_as_stopped():
    clock = Clock()
    out = await verify(FakePrinter(clock, stops_as=PrinterStatus.FAILED), clock)
    assert out.confirmed and out.status == PrinterStatus.FAILED


async def test_no_retries_means_one_attempt():
    clock = Clock()
    out = await verify(FakePrinter(clock, obey_on=None), clock, confirm_s=3, retries=0)
    assert not out.confirmed and out.attempts == 1 and 3.0 <= out.elapsed_s < 3.2

"""Pause verification (safety-critical, local, no LLM, no network round trip beyond the printer itself).

A pause is only "done" when the printer's own state reports it is no longer printing. A command that was sent,
even one the printer acknowledged, is not proof: a firmware that ignores third-party control, a lost MQTT packet
or a dropped connection all look like success from the sender's side.

``pause_and_verify`` sends the pause, watches the printer's reported state, retries once (by default), and returns
an outcome. It is pure apart from the injected callables, so the timing logic is unit-tested without a printer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from printfarm.contracts.messages import PrinterState, PrinterStatus


@dataclass(frozen=True)
class PauseOutcome:
    confirmed: bool
    attempts: int
    elapsed_s: float              # from the first pause command to confirmation (or to giving up)
    status: PrinterStatus | None  # last status the printer reported
    online: bool
    send_errors: tuple[str, ...] = ()


async def pause_and_verify(
    *,
    send_pause: Callable[[], Awaitable[None]],
    current_state: Callable[[], PrinterState | None],
    is_online: Callable[[], bool],
    confirm_s: float,
    retries: int = 1,
    poll_s: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PauseOutcome:
    t0 = clock()
    errors: list[str] = []
    attempts = 0
    for _ in range(1 + max(retries, 0)):
        attempts += 1
        try:
            await send_pause()
        except Exception as exc:  # noqa: BLE001 - keep watching: the printer may still stop (or the next try works)
            errors.append(repr(exc)[:200])
        deadline = clock() + confirm_s
        while True:
            s = current_state()
            if s is not None and s.status not in (PrinterStatus.PRINTING, PrinterStatus.OFFLINE):
                return PauseOutcome(True, attempts, clock() - t0, s.status, is_online(), tuple(errors))
            if clock() >= deadline:
                break
            await sleep(poll_s)
    s = current_state()
    return PauseOutcome(False, attempts, clock() - t0, s.status if s else None, is_online(), tuple(errors))

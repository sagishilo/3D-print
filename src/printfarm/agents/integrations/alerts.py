"""Which platform events become a notification for a person (pure). Critical-only by design: things a person must
act on soon. Everything lands in notify.outbox once (dedupe_key); W2-D delivers it to Telegram / e-mail / WhatsApp
with quiet hours, and until then the dashboard shows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Note:
    topic: str
    severity: str                # info | warning | critical
    title: str
    body: str = ""
    link: str = ""
    dedupe_key: str | None = None
    min_role: str = "operator"


def for_event(kind: str, d: dict, today: date) -> Note | None:
    """``kind``: the event type without prefix and version (e.g. "printer.pause.unconfirmed"); ``d``: its data."""
    match kind:
        case "printer.pause.unconfirmed":
            return Note("safety", "critical", f"{d.get('printer_id')} did not pause",
                        f"A pause was requested ({d.get('reason')}) but the printer still reports "
                        f"{d.get('last_status') or 'no state'}. Check it now.",
                        f"/fleet?printer={d.get('printer_id')}", f"safety:{d.get('printer_id')}:{d.get('plate_id')}")
        case "printer.failure.detected" if d.get("severity") == "critical":
            return Note("safety", "critical", f"{d.get('printer_id')}: {d.get('message')}",
                        f"Detected by {d.get('detector')}; {d.get('action_taken')}.", f"/fleet?printer={d.get('printer_id')}",
                        f"failure:{d.get('printer_id')}:{d.get('plate_id')}:{d.get('detector')}")
        case "fleet.plate.finished" if d.get("succeeded") is False:
            return Note("print_failed", "warning", f"A print failed on {d.get('printer_id')}",
                        f"Plate {d.get('plate_id')} ({d.get('material')} {d.get('color')}) did not finish; its units are "
                        "printed again.", "/live", f"plate_failed:{d.get('plate_id')}")
        case "order.order.needs_attention":
            return Note("order_attention", "warning", f"Order {d.get('order_id')} needs attention", str(d.get("reason") or ""),
                        f"/orders?order={d.get('order_id')}", f"attention:{d.get('order_id')}:{today.isoformat()}")
        case "inventory.alert.raised" if d.get("severity") in ("warning", "critical") and d.get("alert_kind") == "spool_short":
            return Note("stock", "warning", "A plate will run out of filament", str(d.get("message") or ""), "/filament",
                        f"alert:{d.get('alert_id')}")
        case "integrations.sync.finished" if not d.get("ok") and d.get("failures") == 3:
            return Note("integration", "warning", f"Connector {d.get('connector_id')} keeps failing",
                        f"Three syncs in a row failed: {d.get('error')}. It keeps retrying, more slowly.", "/integrations",
                        f"connector:{d.get('connector_id')}:{today.isoformat()}", min_role="owner")
    return None

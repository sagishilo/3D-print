"""Israel Post (Wave 2 / W2-B): parcels sent with Israel Post.

Israel Post has no public API for making labels or reading tracking (its business integrations are arranged with
it directly). So, live, this connector does what can be done without one, and leaves the rest to a person:

* the operator makes the label on Israel Post's business site (or at the counter) and types the tracking number
  here; the connector checks its form (two letters, nine digits, IL, e.g. RR123456789IL; or a domestic number)
  and links the customer to Israel Post's public tracking page;
* this dashboard prints an address label for the parcel (sender and recipient), for when a printed label is enough;
* where the parcel is (in transit, delivered) is set by a person, or seen by the customer on the tracking link.

When Israel Post gives the business an API, ``create_label`` and ``track`` are where it goes: nothing else changes.

Fake mode (the default) makes test tracking numbers and moves each parcel on by itself (in transit, then delivered)
so the whole flow shows without a parcel.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from printfarm.agents.integrations.couriers import CourierConnector, Label, TrackEvent
from printfarm.agents.integrations.framework import SettingSpec, SyncContext


class IsraelPost(CourierConnector):
    id = "israel-post"
    label = "Israel Post"
    tracking_pattern = r"[A-Z]{2}\d{9}[A-Z]{2}|\d{9,15}"
    tracking_link = "https://israelpost.co.il/itemtrace?itemcode={tracking}"
    settings = (
        SettingSpec("sender_name", "Sender name on the printed label", ""),
        SettingSpec("sender_street", "Sender street", ""),
        SettingSpec("sender_city", "Sender city and zip", ""),
        SettingSpec("sender_phone", "Sender phone", ""),
        SettingSpec("service", "Default service (e.g. registered, parcel, express)", "registered",
                    pattern=r"[A-Za-z0-9 _-]{1,40}"),
    )

    def valid_tracking(self, tracking: str) -> bool:
        return super().valid_tracking(tracking.strip().upper())

    async def create_label(self, ctx: SyncContext, order_id: str, ship_to: Mapping, weight_g: int | None,
                           service: str, number_hint: int) -> Label | None:
        if ctx.mode != "live":
            tracking = f"RR{(900_000_000 + number_hint) % 1_000_000_000:09d}IL"
            return Label(tracking, self.tracking_url(tracking))
        return None                      # live: a person makes the label at Israel Post and types its number

    async def track(self, ctx: SyncContext, tracking: str, current: str, checks: int) -> list[TrackEvent]:
        if ctx.mode != "live":
            # the test parcel: in transit at the first check, delivered at the second
            at = ctx.now
            out = [TrackEvent("in_transit", (at - timedelta(seconds=1)).isoformat(), "Accepted at the post office")]
            if checks >= 1:
                out.append(TrackEvent("delivered", at.isoformat(), "Delivered to the recipient"))
            return out
        return []                        # live: no public tracking API; a person moves it on

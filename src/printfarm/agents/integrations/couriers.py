"""Courier connectors (Wave 2 / W2-B): what a courier (Israel Post first; HFD or Cheetah would be adapters like it)
plugs into, and the pure rules of a parcel's life.

A courier may make the label and tracking number itself (``create_label`` returns them), or leave that to a person
(it returns None: the operator makes the label on the courier's own site and types the tracking number here). It
may report where a parcel is (``track``), or not (then a person moves it on: in transit, delivered). Either way the
parcel is one row in integrations.shipments with its status: label_created -> in_transit -> delivered (or returned,
or cancelled while it is only a label).

A carrier typed by hand (a friend's van, a pickup point) is the same parcel without a connector.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from printfarm.agents.integrations.framework import Connector, SyncContext, SyncResult

ORDER = ("label_created", "in_transit", "delivered")
FINAL = {"delivered", "returned", "cancelled"}


@dataclass(frozen=True)
class Label:
    tracking: str
    tracking_url: str = ""
    label_url: str = ""          # "" -> this dashboard prints its own address label


@dataclass(frozen=True)
class TrackEvent:
    status: str                  # in_transit | delivered | returned
    at: str                      # ISO time
    text: str = ""


def can_move(current: str, to: str) -> str | None:
    """Why a parcel may not go from ``current`` to ``to`` (None: it may)."""
    if current == to:
        return f"it is already {to.replace('_', ' ')}"
    if current in FINAL:
        return f"it is {current.replace('_', ' ')}: nothing more happens to it"
    if to == "cancelled" and current != "label_created":
        return "only a label that has not left can be cancelled"
    if to in ORDER and ORDER.index(to) < ORDER.index(current):
        return f"it is {current.replace('_', ' ')} already"
    return None


def latest(events: Sequence[TrackEvent], current: str) -> TrackEvent | None:
    """The newest courier event that moves the parcel on (never back)."""
    best = None
    for e in sorted(events, key=lambda e: e.at):
        target = best.status if best else current
        if can_move(target, e.status) is None and e.status != "cancelled":
            best = e
    return best


def shipment_id(order_id: str, n: int = 1) -> str:
    """A parcel's id: pcl-<order>-<n> (n: the order's how-many-th label, after cancelled ones)."""
    return f"pcl-{order_id}-{n}"[:60]


class CourierConnector(Connector):
    kind = "courier"
    default_interval_s = 900
    tracking_pattern: str = r"[A-Za-z0-9-]{4,40}"
    tracking_link: str = ""      # "...{tracking}..." -> the courier's public tracking page

    def valid_tracking(self, tracking: str) -> bool:
        return bool(re.fullmatch(self.tracking_pattern, tracking.strip()))

    def tracking_url(self, tracking: str) -> str:
        return self.tracking_link.format(tracking=tracking.strip()) if self.tracking_link and tracking else ""

    async def create_label(self, ctx: SyncContext, order_id: str, ship_to: Mapping, weight_g: int | None,
                           service: str, number_hint: int) -> Label | None:        # pragma: no cover - per courier
        raise NotImplementedError

    async def track(self, ctx: SyncContext, tracking: str, current: str, checks: int) -> list[TrackEvent]:   # pragma: no cover
        raise NotImplementedError

    async def sync(self, ctx: SyncContext) -> SyncResult:
        # parcels are followed by the agent's tick (integrations.shipments), not as synced records
        return SyncResult([], dict(ctx.cursor))

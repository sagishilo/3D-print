"""The connector framework (Wave 2 / W2-0): what every outside service (store, invoicing, courier, alert channel)
plugs into, and the pure rules the integrations agent runs them by.

A connector declares its id, kind, label and the secrets it needs (read from ``integrations.env`` on the PC, never
stored in the database or sent to a browser), and implements ``sync``: it fetches what changed since ``cursor`` and
returns the outside records it saw. It does not write our tables itself: the agent maps each record (outside id ->
our id) in one transaction, so a sync that runs twice creates nothing twice, and an unchanged record (same payload
hash) is not touched.

Every connector runs in **fake** mode until an owner chooses live: fake answers come from the connector itself
(deterministic test data), so the dashboard, the tests and CI never call a real service. Connections are outbound
only (polling): nothing on the internet calls this stack.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

BACKOFF_CAP_S = 6 * 3600         # never wait more than six hours after failures
FAILURES_TO_NOTIFY = 3           # this many failures in a row: a person is told (notify.outbox)


@dataclass(frozen=True)
class SecretSpec:
    key: str                     # the name in integrations.env, e.g. SHOPIFY_TOKEN
    label: str
    required: bool = True        # needed for live mode


@dataclass(frozen=True)
class SettingSpec:
    """One setting an owner may change on the Integrations screen (not a secret: a shop name, a switch)."""

    key: str
    label: str
    default: str | int | bool
    pattern: str | None = None   # strings: a full-match regex ("" is always allowed: not set yet)
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Record:
    """One outside record a sync saw, mapped to our id (``internal_id``)."""

    entity: str                  # order | product | customer | shipment | document | …
    external_id: str
    internal_id: str
    payload: Mapping = field(default_factory=dict)

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


@dataclass
class SyncResult:
    records: list[Record] = field(default_factory=list)
    cursor: dict = field(default_factory=dict)       # where to continue next time


@dataclass(frozen=True)
class SyncContext:
    mode: str                                         # fake | live
    cursor: Mapping
    settings: Mapping
    secrets: Mapping[str, str]                        # only this connector's declared keys
    now: datetime
    log: Callable[[str], None] = print


class Connector:
    id: str = ""
    kind: str = ""               # store | invoicing | courier | alerts | demo
    label: str = ""
    secrets: tuple[SecretSpec, ...] = ()
    settings: tuple[SettingSpec, ...] = ()
    default_interval_s: int = 300

    async def sync(self, ctx: SyncContext) -> SyncResult:     # pragma: no cover - each connector implements it
        raise NotImplementedError

    def live_missing(self, values: Mapping[str, str], settings: Mapping) -> list[str]:
        """What live mode still needs (secret keys or settings); empty: live may run."""
        return live_blocked(self.secrets, values)

    def settings_with_defaults(self, stored: Mapping) -> dict:
        return {s.key: stored.get(s.key, s.default) for s in self.settings} | {
            k: v for k, v in stored.items() if k not in {s.key for s in self.settings}}


def clean_settings(specs: Iterable[SettingSpec], change: Mapping) -> dict:
    """An owner's settings change, checked against the connector's specs: known keys, the default's type, the
    pattern or range. Raises ValueError with a message for the dashboard."""
    import re

    by_key = {s.key: s for s in specs}
    out = {}
    for key, value in change.items():
        spec = by_key.get(key)
        if spec is None:
            raise ValueError(f"unknown setting {key!r}")
        if type(value) is not type(spec.default):
            raise ValueError(f"{key} takes a {type(spec.default).__name__} value")
        if isinstance(value, str):
            value = value.strip()
            if value and spec.pattern and not re.fullmatch(spec.pattern, value):
                raise ValueError(f"{spec.label}: {value!r} is not valid")
        if isinstance(value, int) and not isinstance(value, bool) and (
                spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum):
            raise ValueError(f"{spec.label} must be between {spec.minimum} and {spec.maximum}")
        out[key] = value
    return out


class SyncError(Exception):
    """A sync that failed for a reason worth showing on the dashboard (no secret in the message)."""


# ---------------------------------------------------------------- pure scheduling rules


def backoff_s(failures: int, interval_s: int, jitter: float = 0.0) -> float:
    """How long to wait after ``failures`` failures in a row: the interval, doubled per failure, capped at
    BACKOFF_CAP_S; ``jitter`` (0..1) spreads retries so connectors do not all retry at once."""
    if failures <= 0:
        return float(interval_s)
    wait = min(float(BACKOFF_CAP_S), interval_s * (2 ** min(failures, 20)))
    return wait * (1 + 0.1 * jitter)


def next_run(now: datetime, failures: int, interval_s: int, rng: random.Random | None = None) -> datetime:
    return now + timedelta(seconds=backoff_s(failures, interval_s, (rng or random).random()))


def is_due(enabled: bool, next_run_at: datetime | None, now: datetime) -> bool:
    return enabled and (next_run_at is None or next_run_at <= now)


def health(enabled: bool, failures: int, last_ok_at: datetime | None, last_run_at: datetime | None) -> str:
    """off | waiting (never ran) | ok | failing (a failure since the last success) | down (FAILURES_TO_NOTIFY+)."""
    if not enabled:
        return "off"
    if failures >= FAILURES_TO_NOTIFY:
        return "down"
    if failures:
        return "failing"
    return "ok" if last_ok_at or last_run_at else "waiting"


@dataclass(frozen=True)
class Diff:
    create: tuple[Record, ...]
    update: tuple[Record, ...]
    same: int


def diff(records: Iterable[Record], known: Mapping[tuple[str, str], str]) -> Diff:
    """Which records are new, which changed (payload hash) and how many are unchanged. ``known``:
    (entity, external_id) -> payload hash already mapped. A record seen twice in one sync counts once."""
    create, update, same, seen = [], [], 0, set()
    for r in records:
        key = (r.entity, r.external_id)
        if key in seen:
            continue
        seen.add(key)
        if key not in known:
            create.append(r)
        elif known[key] != r.payload_hash:
            update.append(r)
        else:
            same += 1
    return Diff(tuple(create), tuple(update), same)


# ---------------------------------------------------------------- secrets (integrations.env)


def read_env_file(path: str | Path) -> dict[str, str]:
    """KEY=value lines (comments and blanks skipped, quotes stripped). A missing file is no secrets."""
    p = Path(path)
    if not p.is_file():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if v and v != "CHANGE_ME":
                out[k.strip()] = v
    return out


def secrets_status(specs: Iterable[SecretSpec], values: Mapping[str, str]) -> dict[str, bool]:
    """Set or missing per declared key: the only thing about secrets that leaves the agent."""
    return {s.key: bool(values.get(s.key)) for s in specs}


def live_blocked(specs: Iterable[SecretSpec], values: Mapping[str, str]) -> list[str]:
    """Required secrets missing for live mode (empty: live may run)."""
    return [s.key for s in specs if s.required and not values.get(s.key)]


Sync = Callable[[SyncContext], Awaitable[SyncResult]]

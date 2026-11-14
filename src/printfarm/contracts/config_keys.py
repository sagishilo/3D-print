"""Automation settings: the keys of the NATS KV bucket ``config``.

Changed only with ``cmd.coordinator.set_config`` (audited in coordinator.config_changes). Agents read them through
``Agent.setting()``; the dashboard lists them from here. A setting with ``phase`` set arrives in that roadmap phase and
is shown disabled until then, so the dashboard never offers a switch that does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigKey:
    key: str
    label: str
    help: str
    default: object
    phase: int | None = None     # None: in effect now


CONFIG_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey("safety.auto_pause", "Failure detection auto-pause",
              "Pause the printer on a critical finding (thermal runaway, heating failure); every pause is verified", True),
    ConfigKey("inventory.auto_reorder", "Auto-reorder filament",
              "Reorders under the approval limit are placed automatically; off: every reorder waits for your approval", True),
    ConfigKey("qc.agent_enabled", "QC agent module", "Optional · off: operator passes / fails in the QC column", False, phase=5),
    ConfigKey("catalog.auto_queue", "Auto-queue catalog batches", "Queue when demand score ≥ 0.8", False, phase=8),
    ConfigKey("fleet.quiet_hours", "Quiet hours 23:00–07:00", "No new prints start; running prints continue", False, phase=9),
    ConfigKey("trend.enabled", "Trend Analyst module", "Optional · shows Trends on the Social page", False, phase=10),
)
CONFIG_DEFAULTS: dict[str, object] = {k.key: k.default for k in CONFIG_KEYS}

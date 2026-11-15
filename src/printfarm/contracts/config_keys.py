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
    minimum: float | None = None  # numeric settings: the allowed range
    maximum: float | None = None


CONFIG_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey("safety.auto_pause", "Failure detection auto-pause",
              "Pause the printer on a critical finding (thermal runaway, heating failure); every pause is verified", True),
    ConfigKey("inventory.auto_reorder", "Auto-reorder filament",
              "Reorders under the approval limit are placed automatically; off: every reorder waits for your approval", True),
    ConfigKey("orders.auto_personalize", "Auto-start personalization",
              "New orders go straight to nameplate generation; off: you drag them to Personalization", False),
    ConfigKey("orders.auto_schedule", "Auto-schedule printing",
              "Ready models go straight to the print queue; off: you drag them to Slicing / scheduling", False),
    ConfigKey("orders.auto_qc_pass", "Auto-pass quality control",
              "Printed orders pass QC without a check; off: you pass or fail them in the QC column", False),
    ConfigKey("qc.agent_enabled", "QC agent module", "Optional camera-based QC (vision); off: operator checks", False, phase=9),
    ConfigKey("catalog.auto_queue", "Auto-queue catalog batches", "Queue when demand score ≥ 0.8", False, phase=8),
    ConfigKey("fleet.quiet_hours", "Quiet hours 23:00–07:00", "No new prints start; running prints continue", False, phase=9),
    ConfigKey("social.enabled", "Social listening",
              "Score captured posts and draft replies with Claude; off: captures are kept and wait", True),
    ConfigKey("social.intent_threshold", "Lead threshold",
              "Buying intent (0–1, from the model) from which a capture becomes a lead with a reply draft", 0.7,
              minimum=0.3, maximum=0.95),
    ConfigKey("social.prefilter_min", "Prefilter",
              "Similarity to known buyer posts (0–1) below which a capture is ignored without asking the model", 0.3,
              minimum=0.0, maximum=0.9),
    ConfigKey("social.daily_budget_usd", "Daily model budget (USD)",
              "Captures wait once today's Claude spend reaches this; they can be rescored tomorrow", 2.0,
              minimum=0.0, maximum=100.0),
    ConfigKey("trend.enabled", "Trend Analyst module", "Optional · shows Trends on the Social page", False, phase=10),
)
CONFIG_DEFAULTS: dict[str, object] = {k.key: k.default for k in CONFIG_KEYS}

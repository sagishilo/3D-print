"""Runtime settings (environment) and fleet description (TOML)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PrinterCfg:
    id: str
    vendor: str                       # "bambu" | "moonraker"
    bed_mm: tuple[float, float]
    nozzle_mm: float
    material: str
    color: str
    # bambu (local MQTT)
    host: str = ""
    port: int = 8883
    serial: str = ""
    access_code_env: str = ""         # name of the env var holding the LAN access code
    tls: bool = True
    # moonraker
    url: str = ""                     # ws://host:7125/websocket


@dataclass(frozen=True)
class MaterialCfg:
    material: str
    color: str
    grams_per_spool: float
    cost_per_spool: float
    lead_time_days: float
    safety_stock_days: float
    baseline_daily_grams: float
    auto_approve_limit: float
    review_period_days: float = 14


@dataclass(frozen=True)
class SpoolCfg:
    id: str
    material: str
    color: str
    grams: float
    remaining_grams: float | None = None
    loaded_on: str | None = None


@dataclass(frozen=True)
class FleetCfg:
    printers: list[PrinterCfg]
    materials: list[MaterialCfg]
    spools: list[SpoolCfg]
    auto_clear_bed: bool = False      # dev only: pretend an operator cleared the bed
    max_plate_minutes: float = 480
    edge_margin_mm: float = 5
    part_spacing_mm: float = 4
    schedule_interval_s: float = 15
    batch_window_s: float = 3
    sim_print_seconds: float = 20
    font: str = "Noto Sans Hebrew:style=Bold"

    @classmethod
    def load(cls, path: str | Path) -> FleetCfg:
        raw = tomllib.loads(Path(path).read_text())
        fleet = raw.get("fleet", {})
        return cls(
            printers=[PrinterCfg(**{**p, "bed_mm": tuple(p["bed_mm"])}) for p in raw.get("printers", [])],
            materials=[MaterialCfg(**m) for m in raw.get("materials", [])],
            spools=[SpoolCfg(**s) for s in raw.get("spools", [])],
            auto_clear_bed=fleet.get("auto_clear_bed", False),
            max_plate_minutes=fleet.get("max_plate_minutes", 480),
            edge_margin_mm=fleet.get("edge_margin_mm", 5),
            part_spacing_mm=fleet.get("part_spacing_mm", 4),
            schedule_interval_s=fleet.get("schedule_interval_s", 15),
            batch_window_s=fleet.get("batch_window_s", 3),
            sim_print_seconds=raw.get("simulator", {}).get("print_seconds", 20),
            font=raw.get("personalization", {}).get("font", "Noto Sans Hebrew:style=Bold"),
        )


@dataclass(frozen=True)
class Settings:
    nats_url: str = field(default_factory=lambda: os.getenv("NATS_URL", "nats://localhost:4222"))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "postgresql://printfarm:printfarm@localhost:5432/printfarm")
    )
    fleet_config: str = field(default_factory=lambda: os.getenv("FLEET_CONFIG", "config/fleet.toml"))
    artifact_dir: str = field(default_factory=lambda: os.getenv("ARTIFACT_DIR", "var/artifacts"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def fleet(self) -> FleetCfg:
        return FleetCfg.load(self.fleet_config)

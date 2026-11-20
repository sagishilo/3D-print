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
    model: str = ""                   # informational: x1 | p1 | a1 | klipper ...
    # A SIMULATED printer accepts placeholder plate files built from unsliced parts. Never set this for a real printer.
    simulated: bool = False
    # bambu (local MQTT on `host`, implicit FTPS for uploads, port-6000 JPEG camera on P1/A1)
    host: str = ""
    port: int = 8883
    serial: str = ""
    access_code_env: str = ""         # name of the env var holding the LAN access code
    tls: bool = True
    ftp_host: str = ""                # default: host (only the simulator serves FTP elsewhere)
    ftp_port: int = 990
    camera_port: int = 6000           # P1/A1 camera; used by the simulator (bambu-cam has its own setting)
    print_url: str = "ftp:///{file}"  # project_file "url"; {file} = the uploaded file name (verify per firmware)
    use_ams: bool = False
    # average power while printing, in watts, for the electricity cost of each plate (0 = the model's average, else
    # the finance default)
    watts: float = 0
    max_z_mm: float = 0               # build height (bed_mm's third value, or the model's); 0 = not checked
    ams_slots: int = 0                # AMS slots attached (A1 + AMS Lite = 4); 0 = one external spool
    # what is in the AMS slots, in slot order: "PLA/black" (the scheduler takes jobs of any of these filaments)
    ams_filaments: tuple[str, ...] = ()
    # wear before this platform: print hours already on the printer (its own counter), counted into maintenance
    initial_print_hours: float = 0
    # task_id -> the printer's print hours when that task was last done (unknown tasks count from onboarding)
    maintenance_done_hours: tuple[tuple[str, float], ...] = ()
    # moonraker
    url: str = ""                     # ws://host:7125/websocket (HTTP API on the same host and port)
    api_key_env: str = ""             # env var with a Moonraker API key, if the printer requires one

    def loaded(self) -> tuple[tuple[str, str], ...]:
        """The filaments this printer prints without a spool change: its AMS slots, else its one spool."""
        out = [tuple(f.split("/", 1)) for f in self.ams_filaments]
        return tuple(out) if out else ((self.material, self.color),)

    def access_code(self) -> str:
        return os.getenv(self.access_code_env, "") if self.access_code_env else ""

    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "") if self.api_key_env else ""

    def http_base(self) -> str:
        """Moonraker's HTTP API lives on the same host:port as its WebSocket."""
        from urllib.parse import urlparse

        u = urlparse(self.url)
        scheme = "https" if u.scheme == "wss" else "http"
        return f"{scheme}://{u.hostname}:{u.port or (443 if scheme == 'https' else 80)}"


class FleetConfigError(ValueError):
    """A mistake in fleet.toml; the message names the printer and the field."""


def printer_cfg(p: dict) -> PrinterCfg:
    """One [[printers]] table: the model fills what fleet.toml leaves out (bed size, height, watts), then checks."""
    from printfarm.hardware import model

    p = dict(p)
    pid = p.get("id", "?")
    for key, value in p.items():
        if isinstance(value, str) and "<" in value and ">" in value:
            raise FleetConfigError(f"printer {pid}: fill in {key} (still the template's {value})")
    m = model(p.get("model", ""))
    bed = list(p.get("bed_mm") or (m.bed_mm if m else []))
    if len(bed) not in (2, 3) or any(not isinstance(v, int | float) or v <= 0 for v in bed):
        raise FleetConfigError(f"printer {pid}: bed_mm must be [width, depth] or [width, depth, height] in mm")
    p["bed_mm"] = (float(bed[0]), float(bed[1]))
    if len(bed) == 3 and "max_z_mm" not in p:
        p["max_z_mm"] = float(bed[2])
    elif "max_z_mm" not in p and m:
        p["max_z_mm"] = m.bed_mm[2]
    if not p.get("watts") and m:
        p["watts"] = m.watts
    slots = int(p.get("ams_slots", 0))
    if slots < 0 or (m and slots > m.ams_max_slots):
        raise FleetConfigError(f"printer {pid}: ams_slots {slots} is more than a {m.label if m else 'printer'} can take")
    p.setdefault("use_ams", slots > 0)
    if p["use_ams"] and not slots:
        raise FleetConfigError(f"printer {pid}: use_ams = true needs ams_slots (4 for one AMS Lite)")
    fil = tuple(str(f).strip() for f in p.get("ams_filaments", ()))
    if len(fil) > slots:
        raise FleetConfigError(f"printer {pid}: {len(fil)} ams_filaments for {slots} AMS slots")
    if any(f.count("/") != 1 or not all(f.split("/")) for f in fil):
        raise FleetConfigError(f'printer {pid}: each ams_filaments entry is "MATERIAL/color", e.g. "PLA/black"')
    p["ams_filaments"] = fil
    if fil and not p.get("material"):
        p["material"], p["color"] = fil[0].split("/")
    hours = float(p.get("initial_print_hours", 0))
    if hours < 0:
        raise FleetConfigError(f"printer {pid}: initial_print_hours cannot be negative")
    done = p.get("maintenance_done_hours", {}) or {}
    if any(not isinstance(v, int | float) or v < 0 or v > hours for v in done.values()):
        raise FleetConfigError(f"printer {pid}: maintenance_done_hours are print hours between 0 and initial_print_hours")
    p["maintenance_done_hours"] = tuple(sorted((str(k), float(v)) for k, v in done.items()))
    return PrinterCfg(**p)


@dataclass(frozen=True)
class MaterialCfg:
    material: str
    color: str
    grams_per_spool: float
    cost_per_spool_agorot: int        # gross price of one spool, e.g. 8500 = 85.00 ILS
    lead_time_days: float
    safety_stock_days: float
    baseline_daily_grams: float
    auto_approve_limit_agorot: int    # reorders costing more than this need a human approval
    review_period_days: float = 14
    min_stock_grams: float = 0        # dashboard alert below this; the reorder rule keeps stock above it
    reorder_mode: str = "auto"        # auto: orders under the limit go out on their own; manual: always ask
    supplier: str = ""


@dataclass(frozen=True)
class SpoolCfg:
    id: str
    material: str
    color: str
    grams: float
    remaining_grams: float | None = None
    loaded_on: str | None = None
    brand: str = ""
    slot: str | None = None           # where on the printer, e.g. "AMS 1 · slot 1"
    location: str | None = None       # shelf, while not loaded


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
    # [safety]: every pause is verified against the printer's own reported state
    pause_confirm_s: float = 10       # wait this long for PAUSED after each pause attempt
    pause_retries: int = 1            # extra pause attempts before raising "pause unconfirmed"
    start_confirm_s: float = 120      # a started plate must show up as printing within this time
    upload_timeout_s: float = 600     # one plate-file upload (FTPS / HTTP)

    @classmethod
    def load(cls, path: str | Path) -> FleetCfg:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))  # -sig: tolerate a BOM from Windows editors
        fleet = raw.get("fleet", {})
        safety = raw.get("safety", {})
        return cls(
            pause_confirm_s=safety.get("pause_confirm_s", 10),
            pause_retries=safety.get("pause_retries", 1),
            start_confirm_s=safety.get("start_confirm_s", 120),
            upload_timeout_s=safety.get("upload_timeout_s", 600),
            printers=[printer_cfg(p) for p in raw.get("printers", [])],
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
    # The ledger books every entry on the business's local calendar day (month-end and VAT periods).
    business_tz: str = field(default_factory=lambda: os.getenv("BUSINESS_TZ", "Asia/Jerusalem"))

    def fleet(self) -> FleetCfg:
        return FleetCfg.load(self.fleet_config)

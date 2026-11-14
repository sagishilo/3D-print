"""Calibration routines the dashboard can start (cmd.telemetry.calibrate), per printer type.

One table for the telemetry agent (what to send), the gateway (what a printer supports) and the dashboard (labels,
help, how long it takes). Bambu's MQTT commands are community-documented (OpenBambuAPI): verify them on your firmware
before relying on them (roadmap Phase 9), like the print start command.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Routine:
    key: str
    label: str
    help: str
    vendors: tuple[str, ...]          # "bambu" | "moonraker"
    max_minutes: int                  # the agent gives up (and reports a failure) after this long
    bambu_option: int = 0             # bit in the Bambu "calibration" command's option field
    bambu_gcode: str = ""             # or a G-code line for Bambu
    klipper_gcode: str = ""           # G-code script for Klipper ({t} = temperature)
    heater: str = ""                  # PID routines: "nozzle" | "bed"
    default_temp: int = 0


ROUTINES: tuple[Routine, ...] = (
    Routine("home", "Home all axes", "Moves every axis to its endstop. Quick check that the motion system works.",
            ("bambu", "moonraker"), 5, bambu_gcode="G28", klipper_gcode="G28"),
    Routine("bed_level", "Bed leveling", "Probes the bed and stores a new mesh. Takes several minutes.",
            ("bambu", "moonraker"), 20, bambu_option=1 << 1, klipper_gcode="G28\nBED_MESH_CALIBRATE"),
    Routine("vibration", "Vibration compensation",
            "Measures the frame's resonances (Klipper: input shaper, needs an accelerometer). Loud; takes minutes.",
            ("bambu", "moonraker"), 20, bambu_option=1 << 2, klipper_gcode="G28\nSHAPER_CALIBRATE"),
    Routine("motor_noise", "Motor noise cancellation", "Bambu only, on firmware that supports it. Takes minutes.",
            ("bambu",), 20, bambu_option=1 << 3),
    Routine("nozzle_pid", "Nozzle PID tuning", "Heats the nozzle and tunes its temperature control (Klipper).",
            ("moonraker",), 15, klipper_gcode="PID_CALIBRATE HEATER=extruder TARGET={t}", heater="nozzle",
            default_temp=220),
    Routine("bed_pid", "Bed PID tuning", "Heats the bed and tunes its temperature control (Klipper).",
            ("moonraker",), 20, klipper_gcode="PID_CALIBRATE HEATER=heater_bed TARGET={t}", heater="bed",
            default_temp=60),
)
BY_KEY = {r.key: r for r in ROUTINES}
TEMP_LIMITS = {"nozzle": (150, 300), "bed": (40, 110)}


def routines_for(vendor: str) -> list[Routine]:
    return [r for r in ROUTINES if vendor in r.vendors]


def target_temperature(routine: Routine, requested: int | None) -> int | None:
    """The PID target, within the heater's safe range; None for routines that do not heat."""
    if not routine.heater:
        return None
    lo, hi = TEMP_LIMITS[routine.heater]
    t = requested if requested is not None else routine.default_temp
    if not lo <= t <= hi:
        raise ValueError(f"{routine.label}: temperature must be {lo}-{hi} °C")
    return t

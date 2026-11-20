"""Pre-flight network check for every printer in printers.txt (README step 10).

Run it in a container so it sees the network exactly as the stack does:
  docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python scripts/preflight.py

printers.txt, one printer per line:  <id>,<type>,<ip>
  type = x1 | p1 | a1 | a1-mini | klipper        e.g.  bambu-a1,a1,192.168.1.41
Port numbers are the commonly documented defaults; confirm them for your firmware.
Exit code 0 only when every check passes.
"""
import json
import pathlib
import socket
import ssl
import sys
import urllib.request

CHECKS = {
    "x1": [(8883, "tls", "MQTT over TLS (telemetry, commands)"), (990, "tcp", "FTPS (file upload)"),
           (322, "tls", "RTSPS camera")],
    "p1": [(8883, "tls", "MQTT over TLS"), (990, "tcp", "FTPS"), (6000, "tls", "camera frames")],
    "a1": [(8883, "tls", "MQTT over TLS"), (990, "tcp", "FTPS"), (6000, "tls", "camera frames")],
    "a1-mini": [(8883, "tls", "MQTT over TLS"), (990, "tcp", "FTPS"), (6000, "tls", "camera frames")],
    "klipper": [(7125, "moonraker", "Moonraker API"), (80, "webcam", "Crowsnest MJPEG")],
}
TIMEOUT = 4


def check(ip: str, port: int, kind: str) -> tuple[bool, str]:
    try:
        if kind == "moonraker":
            with urllib.request.urlopen(f"http://{ip}:{port}/server/info", timeout=TIMEOUT) as r:
                state = json.load(r).get("result", {}).get("klippy_state", "?")
                return True, f"HTTP 200, klippy_state={state}"
        if kind == "webcam":
            with urllib.request.urlopen(f"http://{ip}:{port}/webcam/?action=snapshot", timeout=TIMEOUT) as r:
                return r.headers.get_content_type() == "image/jpeg", f"HTTP {r.status} {r.headers.get_content_type()}"
        sock = socket.create_connection((ip, port), timeout=TIMEOUT)
        if kind == "tls":
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # printers use self-signed certs
            with ctx.wrap_socket(sock) as tls:
                return True, f"TLS ok ({tls.version()})"
        sock.close()
        return True, "TCP open"
    except (OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:80]


def main() -> None:
    path = pathlib.Path("printers.txt")
    if not path.exists():
        sys.exit("printers.txt not found. Create it: one line per printer  <id>,<type>,<ip>")
    failed = 0
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pid, ptype, ip = (x.strip() for x in line.split(","))
        print(f"\n{pid}  ({ptype} @ {ip})")
        if ptype not in CHECKS:
            print(f"  FAIL  unknown type '{ptype}' (use x1, p1, a1, a1-mini, klipper)")
            failed += 1
            continue
        for port, kind, purpose in CHECKS[ptype]:
            ok, detail = check(ip, port, kind)
            failed += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {port:<5} {purpose:<38} {detail}")
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED: printer stays Not ready'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

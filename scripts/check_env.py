"""Print SET / MISSING for each required .env key. Never prints values."""
import pathlib
import re
import sys

REQUIRED = ["POSTGRES_PASSWORD", "NATS_SERVICES_PASSWORD", "NATS_AGENTS_PASSWORD", "JWT_SECRET",
            "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "SITE_HOSTS"]
# nats.conf substitutes the NATS passwords unquoted; NATS reads a value that starts with a digit as a number
# (and refuses to start), and special characters can break the config too.
NATS_PASSWORD = re.compile(r"^[A-Za-z][A-Za-z0-9]{15,}$")

env = pathlib.Path(__file__).resolve().parent.parent / ".env"
if not env.exists():
    sys.exit("MISSING  .env file (run step 3)")
vals = {}
for line in env.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip("'\"")
bad = 0
for k in REQUIRED:
    ok = bool(vals.get(k)) and vals.get(k) != "CHANGE_ME"
    bad += not ok
    print(f"{'SET    ' if ok else 'MISSING'}  {k}")
hash_ok = vals.get("ADMIN_PASSWORD_HASH", "").startswith("$argon2id$")
print(f"{'OK     ' if hash_ok else 'INVALID'}  ADMIN_PASSWORD_HASH format")
nats_ok = all(NATS_PASSWORD.match(vals.get(k, "")) for k in ("NATS_SERVICES_PASSWORD", "NATS_AGENTS_PASSWORD"))
print(f"{'OK     ' if nats_ok else 'INVALID'}  NATS password format")
if not nats_ok:
    print("         Each NATS password must start with a letter and use only letters and digits (16+).\n"
          "         Fix it in .env (e.g. put the letter n in front), then: docker compose up -d. This is safe at any time.")
sys.exit(1 if bad or not hash_ok or not nats_ok else 0)

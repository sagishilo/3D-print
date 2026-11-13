"""Print SET / MISSING for each required .env key. Never prints values."""
import pathlib
import sys

REQUIRED = ["POSTGRES_PASSWORD", "NATS_SERVICES_PASSWORD", "NATS_AGENTS_PASSWORD", "JWT_SECRET",
            "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "SITE_HOSTS"]
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
sys.exit(1 if bad or not hash_ok else 0)

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
key = vals.get("ANTHROPIC_API_KEY", "")
print(f"{'SET    ' if key else 'not set'}  ANTHROPIC_API_KEY (optional: Social Listening scores posts only with it)")
if key and not key.startswith("sk-ant-"):
    print("         It does not look like an Anthropic API key (they start with sk-ant-).")
remote, recipient = vals.get("BACKUP_REMOTE", ""), vals.get("BACKUP_AGE_RECIPIENT", "")
offsite_ok = (not remote and not recipient) or (bool(remote) and recipient.startswith("age1"))
if not remote and not recipient:
    print("not set  BACKUP_REMOTE (optional: encrypted off-site backups, README \"Off-site backups\")")
else:
    print(f"{'OK     ' if offsite_ok else 'INVALID'}  off-site backups (BACKUP_REMOTE + BACKUP_AGE_RECIPIENT)")
    if not offsite_ok:
        print("         Set both: BACKUP_REMOTE (an rclone remote) and BACKUP_AGE_RECIPIENT (the PUBLIC key, age1...).\n"
              "         Without a recipient nothing is uploaded: an unencrypted backup never leaves this PC.")
sys.exit(1 if bad or not hash_ok or not nats_ok or not offsite_ok else 0)

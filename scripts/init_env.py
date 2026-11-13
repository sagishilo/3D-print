"""Create .env from .env.example: random secrets + admin password hash.

Run from the stack folder (README step 3). Never overwrites an existing .env.
Secrets are generated here and written only to .env; nothing is printed.
"""
import getpass
import pathlib
import secrets
import sys

from argon2 import PasswordHasher

root = pathlib.Path(__file__).resolve().parent.parent
env, example = root / ".env", root / ".env.example"

if env.exists():
    sys.exit(".env already exists: nothing changed. Delete it first only if you really want new secrets "
             "(that also requires wiping the Postgres volume, see README).")

pw = getpass.getpass("Choose the admin password (min 12 characters, input hidden): ")
if len(pw) < 12:
    sys.exit("Too short: nothing written.")
if getpass.getpass("Repeat it: ") != pw:
    sys.exit("Passwords differ: nothing written.")
hosts = input("This PC's fixed LAN IP (Enter to skip, localhost only): ").strip()

values = {
    "POSTGRES_PASSWORD": secrets.token_hex(24),
    # nats.conf substitutes these unquoted, and NATS parses the value: one starting with a digit is read as a
    # number and the server refuses to start. A leading letter keeps it a plain string.
    "NATS_SERVICES_PASSWORD": "n" + secrets.token_hex(24),
    "NATS_AGENTS_PASSWORD": "n" + secrets.token_hex(24),
    "JWT_SECRET": secrets.token_hex(32),
    # single quotes: Docker Compose must not treat the $ signs in the hash as variables
    "ADMIN_PASSWORD_HASH": "'" + PasswordHasher().hash(pw) + "'",
}
out = []
for line in example.read_text().splitlines():
    key = line.split("=", 1)[0]
    if key in values and line.endswith("CHANGE_ME"):
        line = f"{key}={values[key]}"
    elif key == "SITE_HOSTS" and hosts:
        line = f"SITE_HOSTS=localhost, {hosts}"
    out.append(line)
env.write_text("\n".join(out) + "\n")
print(".env created. Secrets were not displayed.")

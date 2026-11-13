"""Print an argon2id hash for ADMIN_PASSWORD_HASH.

Usage (inside the gateway image):  python -m app.hashpw
The password is read without echo and never written to disk.
"""
import getpass
import sys

from argon2 import PasswordHasher


def main() -> None:
    pw = getpass.getpass("New admin password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    if sys.stdin.isatty() and getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords do not match.")
    print(PasswordHasher().hash(pw))


if __name__ == "__main__":
    main()

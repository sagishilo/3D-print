"""Migration bookkeeping: which SQL files are applied, pending, or were changed after they were applied.

Rules (``plan``):
- a migration is identified by its file name and a SHA-256 of its text (line endings normalised, so a Windows
  checkout does not count as a change);
- an applied migration whose file has changed since is an error: fix forward with a new numbered file;
- an applied migration this build does not have is an error: the database is newer than the code (an older image
  is running against it);
- rows recorded before checksums existed (``checksum IS NULL``) are adopted with the current file's checksum.

Pure functions here; ``Database.migrate`` (store/db.py) applies the plan under an advisory lock, and the gateway's
``/health`` reports it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from importlib import resources

LOCK_ID = 4242          # pg_advisory_xact_lock key: one migrator at a time

TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS public.schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());"
    "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT;"
    "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS duration_ms INT"
)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return checksum(self.sql)


@dataclass
class Plan:
    pending: list[Migration] = field(default_factory=list)
    adopt: list[Migration] = field(default_factory=list)        # applied before checksums: record the checksum
    changed: list[str] = field(default_factory=list)            # applied, but the file differs now
    unknown: list[str] = field(default_factory=list)            # applied, but this build has no such file
    applied: int = 0
    latest: str | None = None

    @property
    def problems(self) -> list[str]:
        return ([f"{n} was changed after it was applied: undo the edit and add a new migration instead" for n in self.changed]
                + [f"{n} is applied but this build does not have it: the database is newer than the code" for n in self.unknown])

    @property
    def ok(self) -> bool:
        return not self.changed and not self.unknown

    def summary(self) -> dict:
        return {"applied": self.applied, "pending": [m.name for m in self.pending], "problems": self.problems,
                "latest": self.latest}


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode()).hexdigest()


def bundled() -> list[Migration]:
    """The SQL files shipped in this build, in order (``NNN_name.sql``)."""
    root = resources.files("printfarm.store").joinpath("migrations")
    files = sorted((p for p in root.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [Migration(p.name, p.read_text(encoding="utf-8")) for p in files]


def plan(files: list[Migration], applied: dict[str, str | None]) -> Plan:
    """``applied`` = name -> checksum (None for rows recorded before checksums existed)."""
    by_name = {m.name: m for m in files}
    out = Plan(applied=len(applied))
    for name, digest in sorted(applied.items()):
        m = by_name.get(name)
        if m is None:
            out.unknown.append(name)
        elif digest is None:
            out.adopt.append(m)
        elif digest != m.checksum:
            out.changed.append(name)
    out.pending = [m for m in files if m.name not in applied]
    names = sorted(applied)
    out.latest = names[-1] if names else None
    return out

#!/bin/sh
# PostgreSQL backups for PrintFarm Ops (the "backup" service in docker-compose.yml).
#
#   (default)            every BACKUP_INTERVAL_S seconds: pg_dump -Fc into /backups, keep the newest BACKUP_KEEP
#   now                  one backup, then exit:      docker compose run --rm backup now
#   list                 the backups on disk:        docker compose run --rm backup list
#   restore <file>       replace the database:       docker compose stop gateway coordinator personalization fleet inventory finance telemetry
#                                                    docker compose run --rm backup restore printfarm-20261114-020000.dump
#                                                    docker compose up -d
#
# Postgres is the source of truth (orders, ledger, inventory, and every unsent message in the outboxes), so this
# dump is what to copy somewhere safe. NATS holds only messages in flight and caches that agents rebuild.
set -eu

DIR=/backups
INTERVAL=${BACKUP_INTERVAL_S:-86400}
KEEP=${BACKUP_KEEP:-14}
export PGHOST=${PGHOST:-postgres} PGUSER=${PGUSER:-printfarm} PGDATABASE=${PGDATABASE:-printfarm}

backup_once() {
  f="$DIR/printfarm-$(date -u +%Y%m%d-%H%M%S).dump"
  if pg_dump -Fc -f "$f.part"; then
    mv "$f.part" "$f"
    touch "$DIR/.last-ok"
    echo "$(date -u +%FT%TZ) backup ok: $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.part"
    echo "$(date -u +%FT%TZ) backup FAILED" >&2
    return 1
  fi
  # keep the newest $KEEP
  ls -1t "$DIR"/printfarm-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" && echo "removed old backup $old"
  done
}

mkdir -p "$DIR"
case "${1:-loop}" in
  loop)
    until pg_isready -q; do sleep 2; done
    while true; do
      backup_once || true
      sleep "$INTERVAL"
    done
    ;;
  now)
    backup_once
    ;;
  check)   # health check: a good backup within two intervals
    [ -n "$(find "$DIR/.last-ok" -mmin -"$((2 * INTERVAL / 60))" 2>/dev/null)" ]
    ;;
  list)
    ls -lh "$DIR"/printfarm-*.dump 2>/dev/null || echo "no backups in $DIR yet"
    ;;
  restore)
    name=${2:?usage: restore <file name from 'list'>}
    f="$DIR/$(basename "$name")"
    [ -f "$f" ] || { echo "no such backup: $f" >&2; exit 1; }
    echo "restoring $f into $PGDATABASE (existing tables are replaced)..."
    pg_restore --clean --if-exists --no-owner --single-transaction -d "$PGDATABASE" "$f"
    echo "restored. Start everything again: docker compose up -d"
    ;;
  *)
    echo "usage: backup.sh [loop|now|list|restore <file>]" >&2
    exit 2
    ;;
esac

#!/bin/sh
# PostgreSQL backups for PrintFarm Ops (the "backup" service in docker-compose.yml).
#
#   (default)              every BACKUP_INTERVAL_S seconds: pg_dump -Fc into /backups, keep the newest BACKUP_KEEP,
#                          and, when off-site is set up, an encrypted copy to cloud storage (below)
#   now                    one backup (and off-site copy), then exit:   docker compose run --rm backup now
#   list                   the backups on disk:                         docker compose run --rm backup list
#   restore <file>         replace the database with a local backup:
#                            docker compose stop gateway coordinator personalization fleet inventory finance telemetry social
#                            docker compose run --rm backup restore printfarm-20261114-020000.dump
#                            docker compose up -d
#   drill [file]           practise a restore without touching the live data: the newest (or given) local backup is
#                          restored into a scratch database, its tables counted, and the scratch database dropped
#   offsite-list           the encrypted copies in cloud storage
#   offsite-restore <file> download + decrypt an off-site copy (needs the private key, see below), then restore it
#   offsite-drill [file]   the same, into a scratch database (proves the cloud copy and the key both work)
#   selftest               end-to-end check of encrypt -> upload -> download -> decrypt -> restore, against throwaway
#                          databases and a local folder standing in for the cloud (used by CI)
#
# Off-site copies (README "Off-site backups"): set BACKUP_REMOTE (an rclone remote, e.g. "gdrive:printfarm-backups",
# configured in backup-offsite/rclone.conf) and BACKUP_AGE_RECIPIENT (the PUBLIC key from `age-keygen`). Each dump is
# encrypted on this PC with age before it is uploaded, so the cloud provider only ever sees ciphertext. The PRIVATE key
# never lives on this PC: keep it on paper / a USB stick. Restoring an off-site copy needs it mounted for that one run:
#   docker compose run --rm -v D:\printfarm-key.txt:/key.txt:ro backup offsite-restore printfarm-20261114-020000.dump.age
# Without a recipient nothing is uploaded: an unencrypted dump never leaves this PC.
#
# Status for the dashboard: /backups/status.json (last local and off-site result).
# Postgres is the source of truth (orders, ledger, inventory, settings history in coordinator.config_changes, and every
# unsent message in the outboxes). NATS holds only messages in flight and caches that agents rebuild.
set -eu

DIR=${BACKUP_DIR:-/backups}
STATE="$DIR/.state"
INTERVAL=${BACKUP_INTERVAL_S:-86400}
KEEP=${BACKUP_KEEP:-14}
REMOTE=${BACKUP_REMOTE:-}
RECIPIENTS=${BACKUP_AGE_RECIPIENT:-}
OFFSITE_KEEP_DAYS=${BACKUP_OFFSITE_KEEP_DAYS:-90}
IDENTITY=${BACKUP_AGE_IDENTITY:-/key.txt}
export PGHOST=${PGHOST:-postgres} PGUSER=${PGUSER:-printfarm} PGDATABASE=${PGDATABASE:-printfarm}
export RCLONE_CONFIG=${RCLONE_CONFIG:-/config/rclone/rclone.conf}

now() { date -u +%FT%TZ; }
log() { echo "$(now) $*"; }

# ---------------------------------------------------------------- status.json (read by the gateway)
put() { printf '%s' "$2" > "$STATE/$1"; }
get() { cat "$STATE/$1" 2>/dev/null || true; }
json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n\r'; }
write_status() {
  offsite=false
  [ -n "$REMOTE" ] && [ -n "$RECIPIENTS" ] && offsite=true
  part="$DIR/status.json.part"
  cat > "$part" <<EOF
{"interval_s": $INTERVAL, "keep": $KEEP, "offsite_enabled": $offsite, "offsite_remote": "$(json_str "${REMOTE%%:*}")",
 "offsite_keep_days": $OFFSITE_KEEP_DAYS,
 "local_ok_at": "$(get local_ok_at)", "local_file": "$(get local_file)", "local_bytes": $(get local_bytes | grep -E '^[0-9]+$' || echo 0),
 "local_error": "$(json_str "$(get local_error)")", "local_error_at": "$(get local_error_at)",
 "offsite_ok_at": "$(get offsite_ok_at)", "offsite_file": "$(get offsite_file)",
 "offsite_error": "$(json_str "$(get offsite_error)")", "offsite_error_at": "$(get offsite_error_at)",
 "drill_ok_at": "$(get drill_ok_at)", "drill_tables": $(get drill_tables | grep -E '^[0-9]+$' || echo 0),
 "updated_at": "$(now)"}
EOF
  mv "$part" "$DIR/status.json"
}

# ---------------------------------------------------------------- local dump
backup_once() {
  f="$DIR/printfarm-$(date -u +%Y%m%d-%H%M%S).dump"
  if err=$(pg_dump -Fc -f "$f.part" 2>&1); then
    mv "$f.part" "$f"
    touch "$DIR/.last-ok"
    put local_ok_at "$(now)"; put local_file "$(basename "$f")"; put local_bytes "$(wc -c < "$f" | tr -d ' ')"; put local_error ""
    log "backup ok: $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.part"
    put local_error "pg_dump failed: $err"; put local_error_at "$(now)"
    write_status
    log "backup FAILED: $err" >&2
    return 1
  fi
  # keep the newest $KEEP
  ls -1t "$DIR"/printfarm-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" && log "removed old backup $old"
  done
  offsite_copy "$f" || true
  write_status
}

# ---------------------------------------------------------------- off-site: age-encrypt, then rclone
age_args() { for r in $(echo "$RECIPIENTS" | tr ',' ' '); do printf -- '-r %s ' "$r"; done; }

offsite_copy() {
  [ -n "$REMOTE" ] || return 0
  if [ -z "$RECIPIENTS" ]; then
    put offsite_error "BACKUP_REMOTE is set but BACKUP_AGE_RECIPIENT is not: refusing to upload an unencrypted backup"
    put offsite_error_at "$(now)"
    log "off-site copy SKIPPED: no BACKUP_AGE_RECIPIENT (an unencrypted dump never leaves this PC)" >&2
    return 1
  fi
  enc="$1.age"
  # shellcheck disable=SC2046 # one -r per recipient
  if ! err=$(age $(age_args) -o "$enc" "$1" 2>&1); then
    rm -f "$enc"; put offsite_error "encryption failed: $err"; put offsite_error_at "$(now)"
    log "off-site copy FAILED (encrypt): $err" >&2; return 1
  fi
  if err=$(rclone copy --quiet "$enc" "$REMOTE" 2>&1); then
    put offsite_ok_at "$(now)"; put offsite_file "$(basename "$enc")"; put offsite_error ""
    log "off-site copy ok: $REMOTE/$(basename "$enc")"
    rclone delete --quiet --min-age "${OFFSITE_KEEP_DAYS}d" --include 'printfarm-*.dump.age' "$REMOTE" 2>/dev/null || true
  else
    put offsite_error "upload failed: $err"; put offsite_error_at "$(now)"
    log "off-site copy FAILED (upload): $err" >&2
    rm -f "$enc"; return 1
  fi
  rm -f "$enc"
}

need_remote() { [ -n "$REMOTE" ] || { echo "BACKUP_REMOTE is not set (README: Off-site backups)" >&2; exit 1; }; }

fetch_offsite() {   # $1 = name in the remote; prints the path of the decrypted dump
  need_remote
  [ -f "$IDENTITY" ] || { echo "no private key at $IDENTITY: mount it for this run (-v <key file>:/key.txt:ro)" >&2; exit 1; }
  name=$(basename "$1")
  work=$(mktemp -d)
  rclone copy --quiet "$REMOTE/$name" "$work" || { echo "cannot download $name from $REMOTE" >&2; exit 1; }
  out="$DIR/restored-${name%.age}"
  age -d -i "$IDENTITY" -o "$out" "$work/$name" || { rm -rf "$work"; echo "decryption failed: wrong key?" >&2; exit 1; }
  rm -rf "$work"
  echo "$out"
}

newest_offsite() { rclone lsf --include 'printfarm-*.dump.age' "$REMOTE" | sort | tail -n 1; }

# ---------------------------------------------------------------- restore and drill
restore_file() {
  echo "restoring $1 into $PGDATABASE (existing tables are replaced)..."
  pg_restore --clean --if-exists --no-owner --single-transaction -d "$PGDATABASE" "$1"
  echo "restored. Start everything again: docker compose up -d"
}

drill_file() {   # restore into a scratch database, count the tables, drop it
  scratch="${PGDATABASE}_drill"
  dropdb --if-exists "$scratch" && createdb "$scratch"
  pg_restore --no-owner --single-transaction -d "$scratch" "$1"
  n=$(psql -d "$scratch" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema')")
  dropdb "$scratch"
  put drill_ok_at "$(now)"; put drill_tables "$n"; write_status
  echo "DRILL OK: $(basename "$1") restores cleanly ($n tables)"
}

newest_local() { ls -1t "$DIR"/printfarm-*.dump 2>/dev/null | head -n 1; }

# ---------------------------------------------------------------- self-test (CI)
selftest() {
  until pg_isready -q; do sleep 1; done
  base=$PGDATABASE
  export PGDATABASE="${base}_selftest"
  dropdb --if-exists "$PGDATABASE"; createdb "$PGDATABASE"
  psql -q -c "CREATE TABLE t (id int PRIMARY KEY, note text); INSERT INTO t SELECT g, 'row ' || g FROM generate_series(1, 250) g;"
  tmp=$(mktemp -d)
  age-keygen -o "$tmp/key.txt" 2>/dev/null
  RECIPIENTS=$(age-keygen -y "$tmp/key.txt"); IDENTITY="$tmp/key.txt"
  DIR="$tmp/backups"; STATE="$DIR/.state"; mkdir -p "$STATE"
  export RCLONE_CONFIG_SELFTEST_TYPE=local; REMOTE="selftest:$tmp/cloud"
  backup_once
  enc=$(ls "$tmp/cloud"/printfarm-*.dump.age)
  if head -c 5 "$enc" | grep -q PGDMP; then echo "SELFTEST FAILED: the off-site copy is not encrypted" >&2; exit 1; fi
  grep -q '"offsite_enabled": true' "$DIR/status.json" || { echo "SELFTEST FAILED: status.json" >&2; exit 1; }
  dump=$(fetch_offsite "$(newest_offsite)")
  drill_db="${PGDATABASE}_drill"
  dropdb --if-exists "$drill_db"; createdb "$drill_db"
  pg_restore --no-owner -d "$drill_db" "$dump"
  rows=$(psql -d "$drill_db" -Atc "SELECT count(*) FROM t")
  # Without a recipient nothing may be uploaded.
  before=$(ls "$tmp/cloud" | wc -l)
  RECIPIENTS="" offsite_copy "$dump" 2>/dev/null && { echo "SELFTEST FAILED: uploaded without encryption" >&2; exit 1; }
  [ "$(ls "$tmp/cloud" | wc -l)" = "$before" ] || { echo "SELFTEST FAILED: an unencrypted file reached the cloud" >&2; exit 1; }
  dropdb "$drill_db"; dropdb "$PGDATABASE"; rm -rf "$tmp"
  [ "$rows" = 250 ] || { echo "SELFTEST FAILED: restored $rows rows, expected 250" >&2; exit 1; }
  echo "SELFTEST OK: dump -> age -> rclone -> download -> decrypt -> restore (250 rows)"
}

mkdir -p "$DIR" "$STATE"
case "${1:-loop}" in
  loop)
    until pg_isready -q; do sleep 2; done
    write_status
    while true; do
      backup_once || true
      sleep "$INTERVAL"
    done
    ;;
  now)
    backup_once
    ;;
  check)   # health check: a good local backup within two intervals
    [ -n "$(find "$DIR/.last-ok" -mmin -"$((2 * INTERVAL / 60))" 2>/dev/null)" ]
    ;;
  list)
    ls -lh "$DIR"/printfarm-*.dump 2>/dev/null || echo "no backups in $DIR yet"
    ;;
  restore)
    name=${2:?usage: restore <file name from 'list'>}
    f="$DIR/$(basename "$name")"
    [ -f "$f" ] || { echo "no such backup: $f" >&2; exit 1; }
    restore_file "$f"
    ;;
  drill)
    f=${2:+$DIR/$(basename "$2")}
    f=${f:-$(newest_local)}
    [ -n "$f" ] && [ -f "$f" ] || { echo "no local backup to drill" >&2; exit 1; }
    drill_file "$f"
    ;;
  offsite-list)
    need_remote
    rclone lsl --include 'printfarm-*.dump.age' "$REMOTE"
    ;;
  offsite-restore)
    f=$(fetch_offsite "${2:?usage: offsite-restore <file name from offsite-list>}")
    restore_file "$f"
    ;;
  offsite-drill)
    need_remote
    f=$(fetch_offsite "${2:-$(newest_offsite)}")
    drill_file "$f"
    rm -f "$f"
    ;;
  selftest)
    selftest
    ;;
  *)
    echo "usage: backup.sh [loop|now|check|list|restore <file>|drill [file]|offsite-list|offsite-restore <file>|offsite-drill [file]|selftest]" >&2
    exit 2
    ;;
esac

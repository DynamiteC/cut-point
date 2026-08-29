#!/usr/bin/env bash
#
# Local ClickHouse lifecycle + maintenance for CutPoint (Reading A: local dev /
# demo / tests only; the Cloud Run services do NOT use this instance).
#
# Docker-free by design: this drives the native clickhouse binary that already
# lives in .local-clickhouse/, so no container runtime ever touches the data.
# All state persists under .local-clickhouse/ and survives restarts.
#
# Commands:
#   up        start the server (idempotent), wait until it answers SELECT 1
#   down      stop the server gracefully
#   restart   down then up
#   status    report running / not running and row counts
#   logs      tail the server log
#   backup    consistent dump of the cutpoint database to backups/ (timestamped)
#   restore   restore the cutpoint database from a backup directory
#   maintain  OPTIMIZE tables and prune old backups
#
# Usage: scripts/clickhouse.sh <command> [args]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CH_DIR="$REPO_ROOT/.local-clickhouse"
CH_BIN="$CH_DIR/clickhouse"
CONFIG_SRC="$REPO_ROOT/config/clickhouse/config.xml"
CONFIG_DST="$CH_DIR/config.xml"
PIDFILE="$CH_DIR/clickhouse.pid"
HTTP="http://127.0.0.1:8123"
DB="${CLICKHOUSE_DATABASE:-cutpoint}"
BACKUP_ROOT="$REPO_ROOT/backups/clickhouse"
BACKUP_KEEP="${CLICKHOUSE_BACKUP_KEEP:-7}"

log()  { echo "[clickhouse] $*"; }
die()  { echo "[clickhouse] ERROR: $*" >&2; exit 1; }

require_bin() {
    [[ -x "$CH_BIN" ]] || die "clickhouse binary not found at $CH_BIN (run .local-clickhouse/clickhouse-install.sh)"
}

# The clickhouse-client subcommand of the same binary. --host/--port keep it
# pointed at our local server regardless of any ambient config.
chc() {
    "$CH_BIN" client --host 127.0.0.1 --port 9000 "$@"
}

is_up() {
    [[ "$(curl -s "${HTTP}/?query=SELECT+1" 2>/dev/null)" == "1" ]]
}

server_pid() {
    # Prefer the pidfile; fall back to matching the running process against our
    # own data dir so a stale pidfile does not hide a live server.
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        cat "$PIDFILE"
        return 0
    fi
    pgrep -f "clickhouse server .*${CH_DIR}" 2>/dev/null | head -1 || true
}

cmd_up() {
    require_bin
    if is_up; then
        log "already running and answering on ${HTTP}"
        return 0
    fi
    # Keep the runtime config in sync with the tracked template so startup is
    # deterministic and reproducible from git, not from whatever is lying around.
    # The backup disk path must be absolute, so substitute the repo path here.
    mkdir -p "$BACKUP_ROOT"
    sed "s#BACKUP_DISK_PATH#${BACKUP_ROOT}#g" "$CONFIG_SRC" > "$CONFIG_DST"

    log "starting server (data dir: $CH_DIR)"
    # `server -- --path=...` is the invocation this build accepts; a bare
    # `server --path=` is rejected on 26.x. The config file carries ports and
    # logging; the -- flags pin the on-disk locations.
    nohup "$CH_BIN" server --config-file="$CONFIG_DST" -- \
        --path="$CH_DIR/" \
        --tmp_path="$CH_DIR/tmp/" \
        --user_files_path="$CH_DIR/user_files/" \
        --format_schema_path="$CH_DIR/format_schemas/" \
        >"$CH_DIR/nohup.out" 2>&1 &
    echo $! > "$PIDFILE"

    local waited=0
    until is_up; do
        sleep 1
        waited=$((waited + 1))
        if [[ $waited -ge 30 ]]; then
            die "did not become ready within 30s; see $CH_DIR/server.log"
        fi
    done
    log "ready after ${waited}s (pid $(cat "$PIDFILE"))"
}

cmd_down() {
    local pid
    pid="$(server_pid)"
    if [[ -z "$pid" ]]; then
        log "not running"
        rm -f "$PIDFILE"
        return 0
    fi
    log "stopping pid $pid"
    kill "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
        if [[ $waited -ge 20 ]]; then
            log "graceful stop timed out, sending SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
    done
    rm -f "$PIDFILE"
    log "stopped"
}

cmd_status() {
    if is_up; then
        local pid
        pid="$(server_pid)"
        log "RUNNING (pid ${pid:-unknown}) on ${HTTP}"
        chc --query "SELECT table, sum(rows) AS rows
                     FROM system.parts
                     WHERE database = '${DB}' AND active
                     GROUP BY table ORDER BY table FORMAT PrettyCompact" 2>/dev/null \
            || log "server up but '${DB}' not queryable yet"
    else
        log "NOT running"
        return 1
    fi
}

cmd_logs() {
    tail -n "${1:-50}" "$CH_DIR/server.log"
}

# ---------------------------------------------------------------------------
# Backup: a consistent, dependency-free dump of the cutpoint database.
#
# Uses ClickHouse's native BACKUP statement to a local directory, which is
# atomic and captures table data + metadata together. "Enough for local":
# no object storage, no external tooling, restorable on the same machine.
# ---------------------------------------------------------------------------
cmd_backup() {
    is_up || die "server not running; run 'up' first"
    local stamp name dest
    stamp="$(date +%Y%m%d-%H%M%S)"
    name="${DB}-${stamp}"
    dest="$BACKUP_ROOT/${name}"
    mkdir -p "$BACKUP_ROOT"

    log "backing up database '${DB}' -> ${dest}"
    # BACKUP DATABASE captures every table, including the materialized view and
    # its target, in one consistent snapshot on the local 'backups' disk.
    if chc --query "BACKUP DATABASE ${DB} TO Disk('backups', '${name}')" >/dev/null 2>&1; then
        log "native backup complete: ${dest}"
    else
        # If the backups disk is not available for any reason, fall back to a
        # per-table Native-format export that needs no server-side disk config.
        log "native BACKUP unavailable, falling back to per-table export"
        _tsv_export "$dest"
        log "export backup complete: ${dest}"
    fi
    cmd_maintain --prune-only
}

# TSV fallback export: schema + data per table, no server-side disk needed.
_tsv_export() {
    local dest="$1"
    mkdir -p "$dest"
    local tables
    tables="$(chc --query "SELECT name FROM system.tables WHERE database='${DB}' AND engine NOT LIKE '%View%' FORMAT TSV")"
    for t in $tables; do
        chc --query "SHOW CREATE TABLE ${DB}.${t}" --format TSVRaw > "$dest/${t}.schema.sql"
        chc --query "SELECT * FROM ${DB}.${t} FORMAT Native" > "$dest/${t}.native" 2>/dev/null \
            || die "export of ${t} failed"
    done
    # Record the create statements for views separately so restore can recreate
    # the MV wiring after the base tables are loaded.
    chc --query "SELECT name FROM system.tables WHERE database='${DB}' AND engine LIKE '%View%' FORMAT TSV" \
        | while read -r v; do
            [[ -n "$v" ]] && chc --query "SHOW CREATE TABLE ${DB}.${v}" --format TSVRaw > "$dest/${v}.view.sql"
        done
    date +%Y-%m-%dT%H:%M:%S > "$dest/BACKUP_OK"
}

cmd_restore() {
    is_up || die "server not running; run 'up' first"
    local src="${1:-}"
    [[ -n "$src" && -d "$src" ]] || die "usage: restore <backup-dir>  (see $BACKUP_ROOT)"

    if [[ -f "$src/BACKUP_OK" ]]; then
        log "restoring from TSV/Native export: $src"
        for schema in "$src"/*.schema.sql; do
            [[ -e "$schema" ]] || continue
            local t; t="$(basename "$schema" .schema.sql)"
            chc --multiquery --query "CREATE DATABASE IF NOT EXISTS ${DB}; $(cat "$schema")"
            chc --query "INSERT INTO ${DB}.${t} FORMAT Native" < "$src/${t}.native"
            log "restored table ${t}"
        done
        for view in "$src"/*.view.sql; do
            [[ -e "$view" ]] || continue
            chc --multiquery --query "$(cat "$view")"
            log "recreated view $(basename "$view" .view.sql)"
        done
    else
        # Native BACKUP directory
        local name; name="$(basename "$src")"
        log "restoring from native backup: $name"
        chc --query "RESTORE DATABASE ${DB} FROM Disk('backups', '${name}')"
    fi
    log "restore complete"
}

# ---------------------------------------------------------------------------
# Maintenance: compact parts and prune old backups. Light-touch, safe to run
# repeatedly. --prune-only skips the OPTIMIZE (used after a backup).
# ---------------------------------------------------------------------------
cmd_maintain() {
    local prune_only=false
    [[ "${1:-}" == "--prune-only" ]] && prune_only=true

    if [[ "$prune_only" == "false" ]]; then
        is_up || die "server not running; run 'up' first"
        log "OPTIMIZE tables in '${DB}'"
        local tables
        tables="$(chc --query "SELECT name FROM system.tables WHERE database='${DB}' AND engine LIKE '%MergeTree%' FORMAT TSV")"
        for t in $tables; do
            chc --query "OPTIMIZE TABLE ${DB}.${t} FINAL" 2>/dev/null \
                && log "  optimized ${t}" || log "  skipped ${t}"
        done
    fi

    if [[ -d "$BACKUP_ROOT" ]]; then
        # Keep the newest N backups, delete the rest.
        local count
        count="$(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')"
        if [[ "$count" -gt "$BACKUP_KEEP" ]]; then
            log "pruning old backups (keeping newest ${BACKUP_KEEP} of ${count})"
            # shellcheck disable=SC2012
            ls -1dt "$BACKUP_ROOT"/*/ | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
                log "  removing $old"
                rm -rf "$old"
            done
        fi
    fi
}

main() {
    local cmd="${1:-}"
    shift || true
    case "$cmd" in
        up)       cmd_up "$@" ;;
        down)     cmd_down "$@" ;;
        restart)  cmd_down; cmd_up ;;
        status)   cmd_status "$@" ;;
        logs)     cmd_logs "$@" ;;
        backup)   cmd_backup "$@" ;;
        restore)  cmd_restore "$@" ;;
        maintain) cmd_maintain "$@" ;;
        *)
            cat >&2 <<EOF
usage: scripts/clickhouse.sh <command>
  up | down | restart | status | logs [N]
  backup | restore <dir> | maintain [--prune-only]
EOF
            exit 2
            ;;
    esac
}

main "$@"

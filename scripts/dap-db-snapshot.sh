#!/usr/bin/env bash
#
# dap-db-snapshot.sh — pg_dump the canonical DAP Postgres pod to a
# timestamped file. Designed as the safety net before any DB-write-path
# bug-fix run (#636 cross-run ended_at bleed, #637 tokens/cost rollup,
# and any future Alembic migration). Snapshot → fix → verify; if fix
# misbehaves, restore from snapshot and we have not lost canonical
# state.
#
# Reads DAP_DATABASE_URL from .env.local in the repo root (or from
# the environment if pre-exported). Strips the SQLAlchemy
# ``postgresql+psycopg://`` driver prefix that DAP's app config uses
# down to plain ``postgresql://`` that pg_dump accepts. Strips any
# wrapping single-quotes from the .env.local form.
#
# Usage:
#   ./scripts/dap-db-snapshot.sh                 # take snapshot
#   ./scripts/dap-db-snapshot.sh --dir <path>    # alt output dir
#   ./scripts/dap-db-snapshot.sh --list          # list existing
#   ./scripts/dap-db-snapshot.sh --help
#
# Restore is intentionally NOT automated — running pg_restore against
# the canonical pod is destructive and should be a deliberate
# operator action. ``--help`` prints the recommended manual restore
# incantation.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_SNAPSHOT_DIR="${HOME}/dap-db-snapshots"
SNAPSHOT_DIR="${DAP_DB_SNAPSHOT_DIR:-${DEFAULT_SNAPSHOT_DIR}}"

# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

show_help() {
    cat <<EOF
dap-db-snapshot.sh — pg_dump the canonical DAP Postgres to a timestamped file.

USAGE:
    ./scripts/dap-db-snapshot.sh                 take snapshot (default action)
    ./scripts/dap-db-snapshot.sh --dir <path>    alt output dir
    ./scripts/dap-db-snapshot.sh --list          list existing snapshots
    ./scripts/dap-db-snapshot.sh --help          this message

ENVIRONMENT:
    DAP_DATABASE_URL          source URL (sqlalchemy driver prefix is stripped)
                              if unset, read from .env.local in repo root
    DAP_DB_SNAPSHOT_DIR       output dir override (default: ~/dap-db-snapshots)

SNAPSHOT FORMAT:
    Uses pg_dump -Fc (custom format) — allows selective restore via pg_restore
    and is portable across Postgres minor versions. File extension: .dump.

MANUAL RESTORE (DESTRUCTIVE — operator-only):
    # 1. Verify the snapshot first
    pg_restore --list <snapshot.dump> | head

    # 2. Restore (overwrites existing data — pg_restore --clean drops + recreates)
    pg_restore --clean --if-exists --no-owner --dbname="<DAP_DATABASE_URL_clean>" \\
        <snapshot.dump>

    # 3. Verify row counts vs pre-snapshot baseline
    psql "<DAP_DATABASE_URL_clean>" -c "SELECT count(*) FROM runs;"

    Always restore against an isolated DB first (a local pg, a staging copy)
    before touching the canonical pod.
EOF
}

ACTION="snapshot"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            show_help
            exit 0
            ;;
        --list)
            ACTION="list"
            shift
            ;;
        --dir)
            SNAPSHOT_DIR="${2:?--dir requires a path argument}"
            shift 2
            ;;
        *)
            echo "Unknown arg: $1" >&2
            echo "Try --help" >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"
}

err() {
    echo "ERROR: $*" >&2
    exit 1
}

# Read DAP_DATABASE_URL from environment if set, otherwise from .env.local.
# Strip the SQLAlchemy driver prefix (postgresql+psycopg:// → postgresql://)
# and any wrapping quotes. Returns the cleaned URL on stdout.
resolve_database_url() {
    local raw="${DAP_DATABASE_URL:-}"
    if [[ -z "$raw" ]]; then
        local env_file="${REPO_ROOT}/.env.local"
        [[ -f "$env_file" ]] || err "DAP_DATABASE_URL not set and ${env_file} does not exist."
        raw="$(grep -E '^DAP_DATABASE_URL=' "$env_file" | head -1 | cut -d= -f2- || true)"
        [[ -n "$raw" ]] || err "DAP_DATABASE_URL not found in ${env_file}."
    fi

    # Strip wrapping single or double quotes (the .env.local form is
    # often DAP_DATABASE_URL='postgresql+psycopg://...').
    raw="${raw#\'}"
    raw="${raw%\'}"
    raw="${raw#\"}"
    raw="${raw%\"}"

    # Strip the SQLAlchemy driver suffix (e.g. postgresql+psycopg://,
    # postgresql+asyncpg://) down to the bare postgresql:// scheme that
    # pg_dump / pg_restore accept.
    if [[ "$raw" =~ ^postgresql\+[a-z0-9_]+:// ]]; then
        raw="postgresql://${raw#postgresql+*://}"
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

action_list() {
    if [[ ! -d "$SNAPSHOT_DIR" ]]; then
        echo "No snapshots — directory does not exist: ${SNAPSHOT_DIR}"
        return 0
    fi
    local count
    count=$(find "$SNAPSHOT_DIR" -maxdepth 1 -name '*.dump' -type f | wc -l)
    if [[ "$count" -eq 0 ]]; then
        echo "No snapshots in ${SNAPSHOT_DIR}"
        return 0
    fi
    echo "Snapshots in ${SNAPSHOT_DIR}:"
    # ls -lh sorted by mtime, newest first
    ls -lhrt "$SNAPSHOT_DIR"/*.dump | awk '{printf "  %s  %s  %s\n", $5, $6 " " $7 " " $8, $NF}'
}

action_snapshot() {
    command -v pg_dump >/dev/null 2>&1 || err "pg_dump not found in PATH — install postgresql-client."

    local database_url
    database_url="$(resolve_database_url)"

    mkdir -p "$SNAPSHOT_DIR"

    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
    local out_file="${SNAPSHOT_DIR}/dap-db-${timestamp}.dump"

    log "Snapshotting to ${out_file}"

    # -Fc = custom format, the most portable + selective-restore-friendly
    # --no-owner = don't write ownership statements; restore doesn't need them
    # --no-privileges = same idea for ACLs
    # --quote-all-identifiers = defensive against pg version skew
    if ! pg_dump \
        --format=custom \
        --no-owner \
        --no-privileges \
        --quote-all-identifiers \
        --file="${out_file}" \
        "${database_url}"
    then
        rm -f "${out_file}"
        err "pg_dump failed — snapshot NOT written."
    fi

    local size
    size="$(du -h "${out_file}" | cut -f1)"
    log "Snapshot complete: ${out_file} (${size})"
    log "Verify with: pg_restore --list ${out_file} | head"
    log "Restore guidance: ${0} --help"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "$ACTION" in
    snapshot)
        action_snapshot
        ;;
    list)
        action_list
        ;;
    *)
        err "Unknown action: ${ACTION}"
        ;;
esac

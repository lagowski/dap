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
# ``postgresql+psycopg://`` driver prefix down to plain
# ``postgresql://`` that pg_dump accepts. Strips wrapping quotes.
#
# Auto-detects the server major version and chooses between host
# ``pg_dump`` (when host major >= server major) and a Docker
# ``postgres:N`` fallback (when host is too old). Same logic for
# ``pg_restore`` on the verify path. Override via ``--no-docker``
# (forbid fallback) or ``--docker-image postgres:N`` (pin a
# specific tag).
#
# Usage:
#   ./scripts/dap-db-snapshot.sh                       # take snapshot
#   ./scripts/dap-db-snapshot.sh --dir <path>          # alt output dir
#   ./scripts/dap-db-snapshot.sh --list                # list existing
#   ./scripts/dap-db-snapshot.sh --no-docker           # forbid Docker fallback
#   ./scripts/dap-db-snapshot.sh --docker-image postgres:18  # pin tag
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
DEFAULT_DOCKER_IMAGE="${DAP_DB_DOCKER_IMAGE:-postgres:18}"

# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

show_help() {
    cat <<EOF
dap-db-snapshot.sh — pg_dump the canonical DAP Postgres to a timestamped file.

USAGE:
    ./scripts/dap-db-snapshot.sh                              take snapshot (default action)
    ./scripts/dap-db-snapshot.sh --dir <path>                 alt output dir
    ./scripts/dap-db-snapshot.sh --list                       list existing snapshots
    ./scripts/dap-db-snapshot.sh --no-docker                  forbid Docker fallback
    ./scripts/dap-db-snapshot.sh --docker-image postgres:N    pin a specific image
    ./scripts/dap-db-snapshot.sh --help                       this message

ENVIRONMENT:
    DAP_DATABASE_URL          source URL (SQLAlchemy driver prefix is stripped)
                              if unset, read from .env.local in repo root
    DAP_DB_SNAPSHOT_DIR       output dir override (default: ~/dap-db-snapshots)
    DAP_DB_DOCKER_IMAGE       Docker image override (default: postgres:18)

SERVER-VERSION HANDLING:
    pg_dump requires host major >= server major. The script auto-detects the
    server major and chooses between host pg_dump (when compatible) and a
    Docker postgres:N fallback (when host is too old).

    Auto-detection probes the server via host psql first, then falls back to
    docker psql in the chosen image if host psql is missing.

    --no-docker forbids the fallback; if the host pg_dump is too old, the
    script aborts with an actionable error rather than silently downgrading
    to a partial dump.

SNAPSHOT FORMAT:
    Uses pg_dump -Fc (custom format) — allows selective restore via pg_restore
    and is portable across Postgres minor versions. File extension: .dump.

MANUAL RESTORE (DESTRUCTIVE — operator-only):
    # 1. Verify the snapshot first
    ./scripts/dap-db-snapshot.sh --list   # shows the file you want
    pg_restore --list <snapshot.dump> | head
    # (use docker postgres:N pg_restore --list if host major is too old)

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
ALLOW_DOCKER=1
DOCKER_IMAGE="${DEFAULT_DOCKER_IMAGE}"

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
        --no-docker)
            ALLOW_DOCKER=0
            shift
            ;;
        --docker-image)
            DOCKER_IMAGE="${2:?--docker-image requires an image tag (e.g., postgres:18)}"
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
#
# Bug 1 fix (#655): the prior v1 used ``[[ "$raw" =~ ^postgresql\+[...] ]]``
# for the prefix strip. In production on dixter-pc 2026-06-05, that
# conditional silently did NOT fire even when the input visibly matched
# the regex. pg_dump then fell back to a local Unix socket and produced
# a 0-byte file. Replaced with ``sed -E`` which is portable and has
# no regex-engine surprises.
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
    # pg_dump / pg_restore accept. ``sed -E`` over ``[[ =~ ]]`` — see
    # bug-1 fix note in the docstring above.
    raw="$(echo "$raw" | sed -E 's|^postgresql\+[a-z0-9_]+://|postgresql://|')"

    echo "$raw"
}

# Parse a major version number from ``pg_dump --version`` or ``pg_restore --version``
# output. Returns just the integer (e.g. ``16`` from ``pg_dump (PostgreSQL) 16.14``).
parse_tool_major() {
    local tool="$1"
    "$tool" --version 2>/dev/null | grep -oE '[0-9]+' | head -1
}

# Detect the server's major version by querying SHOW server_version_num.
# Tries host psql first (cheap, no docker overhead). Falls back to
# docker postgres:N psql if host psql isn't installed. Returns just
# the major as an int (e.g. ``18``).
detect_server_major() {
    local url="$1"
    local server_version_num=""

    if command -v psql >/dev/null 2>&1; then
        server_version_num="$(psql "$url" -tAc "SHOW server_version_num" 2>/dev/null || true)"
    fi

    if [[ -z "$server_version_num" ]]; then
        if [[ "$ALLOW_DOCKER" -eq 0 ]]; then
            err "host psql is unavailable and --no-docker forbids the Docker fallback. Install postgresql-client or rerun without --no-docker."
        fi
        command -v docker >/dev/null 2>&1 || err "host psql is unavailable AND docker is not installed. Install one of them: postgresql-client (for host psql) or docker.io (for fallback)."
        server_version_num="$(docker run --rm --network host "${DOCKER_IMAGE}" \
            psql "$url" -tAc "SHOW server_version_num" 2>/dev/null || true)"
    fi

    [[ -n "$server_version_num" ]] || err "Could not detect server version. Check DAP_DATABASE_URL and network reachability to the Postgres host."

    # server_version_num format: 180003 for 18.x, 160014 for 16.x.
    # Major is the first 1-2 digits.
    if [[ "${server_version_num:0:2}" =~ ^[0-9]+$ ]] && [[ "${server_version_num:0:2}" -ge 10 ]]; then
        echo "${server_version_num:0:2}"
    else
        echo "${server_version_num:0:1}"
    fi
}

# Decide whether to invoke pg_dump via host binary or via docker.
# Echoes "host" or "docker" on stdout.
choose_pg_dump_runner() {
    local server_major="$1"
    local host_major=""

    if command -v pg_dump >/dev/null 2>&1; then
        host_major="$(parse_tool_major pg_dump)"
    fi

    if [[ -n "$host_major" ]] && [[ "$host_major" -ge "$server_major" ]]; then
        echo "host"
        return
    fi

    if [[ "$ALLOW_DOCKER" -eq 0 ]]; then
        if [[ -z "$host_major" ]]; then
            err "Host pg_dump is not installed and --no-docker forbids the Docker fallback. Install postgresql-client-${server_major} or rerun without --no-docker."
        fi
        err "Host pg_dump ${host_major} is older than server major ${server_major} and --no-docker forbids the Docker fallback. Install postgresql-client-${server_major} or rerun without --no-docker."
    fi

    command -v docker >/dev/null 2>&1 || err "Docker fallback needed (host pg_dump major < server major) but docker is not installed. Install docker.io or use --no-docker after installing postgresql-client-${server_major}."

    echo "docker"
}

# Decide whether to invoke pg_restore via host binary or via docker.
# Same logic shape as choose_pg_dump_runner; separate function because
# the host pg_restore version may differ from the host pg_dump version
# (some distros package them independently).
choose_pg_restore_runner() {
    local server_major="$1"
    local host_major=""

    if command -v pg_restore >/dev/null 2>&1; then
        host_major="$(parse_tool_major pg_restore)"
    fi

    if [[ -n "$host_major" ]] && [[ "$host_major" -ge "$server_major" ]]; then
        echo "host"
        return
    fi

    # --no-docker doesn't fail-loud here; pg_restore --list is just the
    # post-snapshot self-verify step. If the host pg_restore is too old
    # AND docker isn't available, the snapshot itself still succeeded —
    # we just skip the verify and warn.
    if [[ "$ALLOW_DOCKER" -eq 0 ]] || ! command -v docker >/dev/null 2>&1; then
        echo "skip"
        return
    fi

    echo "docker"
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
    local database_url
    database_url="$(resolve_database_url)"

    log "Detecting server version..."
    local server_major
    server_major="$(detect_server_major "$database_url")"
    log "Server major: ${server_major}"

    local runner
    runner="$(choose_pg_dump_runner "$server_major")"
    log "pg_dump runner: ${runner}$( [[ "$runner" == "docker" ]] && echo " (image: ${DOCKER_IMAGE})" )"

    mkdir -p "$SNAPSHOT_DIR"

    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
    local out_file="${SNAPSHOT_DIR}/dap-db-${timestamp}.dump"

    log "Snapshotting to ${out_file}"

    # pg_dump invocation flags (shared between host + docker paths):
    # --format=custom         portable, supports selective restore
    # --no-owner              don't write ownership statements
    # --no-privileges         don't write ACLs
    # --quote-all-identifiers defensive against pg version skew
    if [[ "$runner" == "host" ]]; then
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
    else
        # Docker path: mount the snapshot dir, write to /dumps inside.
        local docker_out_path="/dumps/dap-db-${timestamp}.dump"
        if ! docker run --rm --network host \
            -v "${SNAPSHOT_DIR}:/dumps" \
            -e DB_URL="${database_url}" \
            "${DOCKER_IMAGE}" \
            pg_dump \
                --format=custom \
                --no-owner \
                --no-privileges \
                --quote-all-identifiers \
                --file="${docker_out_path}" \
                "${database_url}"
        then
            rm -f "${out_file}"
            err "pg_dump (via docker ${DOCKER_IMAGE}) failed — snapshot NOT written."
        fi
    fi

    # Bug 3 guard (#655): pg_dump's exit code 0 + empty file is a known
    # failure mode (URL-strip silently misfires → fallback to local Unix
    # socket → "successful" 0-byte file). Always verify the file has
    # content before declaring success.
    if [[ ! -s "${out_file}" ]]; then
        rm -f "${out_file}"
        err "pg_dump produced an empty file (exit 0 but no data). Likely DB_URL is malformed and pg_dump fell back to a local socket. Check resolved URL: ${database_url}"
    fi

    local size
    size="$(du -h "${out_file}" | cut -f1)"
    log "Snapshot complete: ${out_file} (${size})"

    # Post-snapshot verify: run pg_restore --list to confirm the archive
    # is structurally readable. Same version-skew handling as the dump
    # path. Failure here doesn't roll back the snapshot — it just warns
    # the operator that the file may not be readable without the right
    # pg_restore version.
    local restore_runner
    restore_runner="$(choose_pg_restore_runner "$server_major")"
    case "$restore_runner" in
        host)
            log "Verifying with: pg_restore --list ${out_file} | head"
            pg_restore --list "${out_file}" | head -3 || log "WARN: pg_restore --list failed; archive may need a different pg_restore version to read."
            ;;
        docker)
            log "Verifying with: docker run --rm ${DOCKER_IMAGE} pg_restore --list /dumps/dap-db-${timestamp}.dump | head"
            docker run --rm -v "${SNAPSHOT_DIR}:/dumps" "${DOCKER_IMAGE}" \
                pg_restore --list "/dumps/dap-db-${timestamp}.dump" | head -3 || \
                log "WARN: docker pg_restore --list failed; archive may be corrupt."
            ;;
        skip)
            log "WARN: skipping post-snapshot verify — neither host pg_restore (compatible) nor docker available. The dump file exists at ${out_file} but has not been structurally validated."
            ;;
    esac

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

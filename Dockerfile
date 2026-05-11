# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# DAP all-in-one container (#302 sub-D1).
#
# Three-stage build:
#   1. engine-builder — Python 3.13, uv, sync deps + first-party packages
#   2. dashboard-builder — Node 20 + pnpm, build Next.js standalone bundle
#   3. runtime — minimal Python slim with both apps + a tiny entrypoint
#
# Exposes:
#   - 7333 — engine (FastAPI)
#   - 3000 — dashboard (Next.js standalone)
#
# Runs as non-root (UID 1000). Healthcheck on engine /health.
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=20

# ---------------------------------------------------------------------------
# Stage 1 — Engine deps + first-party packages
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS engine-builder

# uv installs into /usr/local/bin/uv. We pin via the official Astral image
# so the version stays predictable across rebuilds.
COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /uvx /usr/local/bin/

# Build dependencies for psycopg + bcrypt (compiled extensions) — slim images
# don't carry libpq-dev / build-essential.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy the workspace metadata so ``uv sync`` resolves the dep graph
# without yet pulling first-party source. Lets the deps layer cache
# across edits to ``apps/*/src/`` etc. Per-package READMEs come along
# because each pyproject.toml declares ``readme = "README.md"`` and
# hatchling refuses to build without it.
COPY pyproject.toml uv.lock README.md ./
COPY apps/cli/pyproject.toml apps/cli/README.md ./apps/cli/
COPY apps/engine/pyproject.toml apps/engine/README.md ./apps/engine/
COPY packages/cortex/pyproject.toml packages/cortex/README.md ./packages/cortex/
COPY packages/prompt-dsl/pyproject.toml packages/prompt-dsl/README.md ./packages/prompt-dsl/
COPY packages/runtimes/pyproject.toml packages/runtimes/README.md ./packages/runtimes/
COPY packages/types/pyproject.toml packages/types/README.md ./packages/types/

# Create empty package stubs so the dep resolver can validate the
# workspace layout without touching real source. They get overwritten
# in the COPY below.
RUN mkdir -p apps/cli/src/dap_cli apps/engine/src/dap_engine \
    packages/cortex/src/cortex packages/prompt-dsl/src/dap_prompt_dsl \
    packages/runtimes/src/dap_runtimes packages/types/src/dap_types \
    && touch apps/cli/src/dap_cli/__init__.py \
    && touch apps/engine/src/dap_engine/__init__.py \
    && touch packages/cortex/src/cortex/__init__.py \
    && touch packages/prompt-dsl/src/dap_prompt_dsl/__init__.py \
    && touch packages/runtimes/src/dap_runtimes/__init__.py \
    && touch packages/types/src/dap_types/__init__.py

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/dap/.venv

# First-pass sync: resolve + install third-party deps. ``--no-install-workspace``
# keeps the workspace members out so they don't fail without sources;
# we copy those in the next layer and re-sync.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Now copy the actual first-party source.
COPY apps/cli/src ./apps/cli/src
COPY apps/engine/src ./apps/engine/src
COPY packages/cortex/src ./packages/cortex/src
COPY packages/prompt-dsl/src ./packages/prompt-dsl/src
COPY packages/runtimes/src ./packages/runtimes/src
COPY packages/types/src ./packages/types/src

# Second-pass sync: install every workspace member (--all-packages)
# as **non-editable** wheels so the source tree doesn't need to be
# copied into the runtime image (the venv carries everything).
# Editable installs would leave ``.pth`` pointers to ``/build/...``
# that don't exist at runtime — that's how the first cut of this
# Dockerfile produced ``ModuleNotFoundError: dap_engine``.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --no-editable


# ---------------------------------------------------------------------------
# Stage 2 — Dashboard (Next.js standalone bundle)
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS dashboard-builder

WORKDIR /build/apps/dashboard

RUN --mount=type=cache,target=/root/.cache/corepack \
    corepack enable && corepack prepare pnpm@10.0.0 --activate

# Lockfile first — pnpm cache survives source edits.
COPY apps/dashboard/package.json apps/dashboard/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY apps/dashboard/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build


# ---------------------------------------------------------------------------
# Stage 3 — Runtime image
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Runtime-only deps for psycopg + Node (for next start). Build tools
# stay in the builder stage — they don't ship.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        nodejs \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — matches the standard kubernetes / OpenShift expectation.
ARG APP_UID=1000
RUN groupadd --gid ${APP_UID} dap \
    && useradd --uid ${APP_UID} --gid dap --create-home --shell /bin/bash dap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/dap/.venv/bin:$PATH" \
    DAP_DATA_DIR=/data \
    DAP_DB_PATH=/data/state.db \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT_ENGINE=7333 \
    PORT_DASHBOARD=3000

# Engine venv from stage 1.
COPY --from=engine-builder /opt/dap/.venv /opt/dap/.venv

# Dashboard standalone bundle from stage 2. Next's standalone output
# lands ``server.js`` at the bundle root with its own minimal
# ``node_modules``; we copy the static assets (``.next/static``) next
# to it. The dashboard has no ``public/`` dir today — the build
# doesn't fail without one, and adding one would just bake empty
# bytes into the image.
COPY --from=dashboard-builder /build/apps/dashboard/.next/standalone /opt/dap/dashboard/
COPY --from=dashboard-builder /build/apps/dashboard/.next/static /opt/dap/dashboard/.next/static

# Entrypoint: spawn engine + dashboard, propagate signals through tini.
COPY --chown=dap:dap docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN install -d -o dap -g dap /data
VOLUME /data

USER dap
WORKDIR /home/dap

EXPOSE 7333 3000

# 30s start_period covers cold-start migrations + uv resolve on big DBs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --silent --fail "http://127.0.0.1:${PORT_ENGINE}/health" || exit 1

LABEL org.opencontainers.image.title="DAP" \
      org.opencontainers.image.description="Deterministic Agent Pipeline — engine + dashboard, all-in-one" \
      org.opencontainers.image.source="https://github.com/rafeekpro/dap" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="rafeekpro"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]

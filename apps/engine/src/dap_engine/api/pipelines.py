"""REST CRUD for pipelines."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from dap_types import Pipeline
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.agents import build_agent_export_payload
from dap_engine.api.deps import get_session
from dap_engine.api.schemas import (
    PIPELINE_EXPORT_SCHEMA_VERSION,
    AgentExportPayload,
    PipelineExport,
    PipelineExportPayload,
    PipelineImportRequest,
)
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.contracts import AgentCreate, PipelineCreate, PipelineUpdate
from dap_engine.execution import ValidationResult, validate_pipeline_dag
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM
from dap_engine.version import __version__

logger = logging.getLogger("dap.engine.pipelines")

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _enforce_min_dap_version(payload: PipelineImportRequest) -> None:
    """Reject bundles that require a newer DAP engine than this instance."""
    required = payload.min_dap_version
    if required is None:
        return

    try:
        required_version = Version(required)
    except InvalidVersion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Invalid min_dap_version in pipeline bundle. Expected a "
                f"valid version like 'MAJOR.MINOR.PATCH' or 'MAJOR.MINOR.PATCH-rc1', "
                f"got '{required}'."
            ),
        ) from exc

    current_version = Version(__version__)
    if current_version < required_version:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Bundle requires DAP >= {required}, but this instance is {__version__}. "
                "Update DAP before importing this bundle."
            ),
        )


@router.get("")
def list_pipelines(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_pipelines(
        session,
        actor_id=user.id,
        is_admin=user.is_superuser,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [p.model_dump(mode="json") for p in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _enforce_validation(payload: PipelineCreate, session: Session) -> ValidationResult:
    """Run the DAG validator and raise 422 on any errors.

    Used by ``create_pipeline`` and ``update_pipeline`` so a structurally
    broken or cohesion-incomplete pipeline can never land in the DB.
    The standalone ``POST /pipelines/validate`` endpoint stays intact —
    it's still useful for live feedback in the Pipeline Designer
    *before* the user clicks Save.

    Warnings are not fatal — they're operator hints (unused outputs,
    conflicting writers, etc.) and get logged here so operators see
    them in engine output. They also ride along on the 422 ``detail``
    when validation does fail, in case a related warning makes the
    error easier to interpret.
    """
    result = validate_pipeline_dag(payload, session)
    if result.warnings:
        logger.warning(
            "pipeline validation warnings: %s",
            "; ".join(result.warnings),
        )
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "errors": result.errors,
                "warnings": result.warnings,
            },
        )
    return result


def _update_to_create_shape(payload: PipelineUpdate) -> PipelineCreate:
    """Adapt a ``PipelineUpdate`` into a ``PipelineCreate`` for the validator.

    The DAG validator only inspects ``entry_point`` / ``nodes`` /
    ``edges`` / ``defaults`` — fields shared between the two types.
    Update-specific nullables (``name``, ``description``) get
    placeholders that satisfy the Create model's ``min_length=1``
    constraint without leaking into the actual DB write (the repo
    handles the update via the original ``PipelineUpdate``).
    """
    return PipelineCreate(
        name=payload.name or "(unchanged)",
        description=payload.description or "",
        schema_version=payload.schema_version,
        state_schema_ref=payload.state_schema_ref,
        entry_point=payload.entry_point,
        nodes=payload.nodes,
        edges=payload.edges,
        defaults=payload.defaults,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Pipeline)
def create_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    """Create a pipeline (v1). Rejects invalid DAGs with 422 (#120).

    Validation happens *before* the DB write so a broken pipeline
    can never become persistent. The same checks the Pipeline
    Designer surfaces interactively are now enforced server-side.
    """
    _enforce_validation(payload, session)
    return repo.create_pipeline(session, payload, user_id=user.id)


@router.post("/validate", response_model=ValidationResult)
def validate_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
    _user: UserORM = Depends(current_active_user),
) -> ValidationResult:
    """Pre-save DAG validation — used by Pipeline Designer before submitting.

    Returns 200 with `valid: false` and a list of errors when invalid;
    422 only on Pydantic-level errors (malformed body). The save
    endpoints (``POST`` / ``PUT``) call the same validator and turn
    any errors into 422 — this endpoint stays as the live-feedback
    surface so the Designer can show diagnostics without committing.

    Auth-required even though it's read-only: the validator hits the
    agents table to check ``node.agent_id`` references, so leaving it
    open would leak which agent ids exist in the DB to anonymous
    callers. The ``_user`` parameter exists purely to wire the auth
    dependency — its value isn't read, the leading underscore signals
    that to readers and to ``ruff`` rule ``PLW0613``.
    """
    return validate_pipeline_dag(payload, session)


def _materialise_pipeline_import(
    payload: PipelineImportRequest,
    session: Session,
    user: UserORM,
) -> Pipeline:
    """Create a new pipeline (v1) from a parsed import payload.

    Shared body for ``POST /pipelines/import`` (file upload) and
    ``POST /pipelines/import-from-url`` (private template registry,
    #385). Caller controls *how* the payload arrived; this function
    owns the bundled-agent registration + DAG validation + pipeline
    creation.

    Two paths share this code:

    - **Pipeline-only** (``bundled_agents`` absent): referenced
      agents must already exist in this DB or the DAG validator
      returns 422.
    - **Bundle** (``bundled_agents`` present, #126): creates each
      bundled agent first, builds an ``old_id → new_id`` remap,
      rewrites every ``node.agent_id`` in the pipeline payload,
      then runs the standard validator + create path. The whole
      thing rides the request session, so a failure anywhere
      rolls back the agents created earlier — no orphans land in
      the DB.

    Errors raise ``HTTPException`` shapes identical to the legacy
    file-upload endpoint, so client error-handling code that worked
    for ``/pipelines/import`` works unchanged here.
    """
    _enforce_min_dap_version(payload)

    bundled = payload.bundled_agents
    pipeline_payload = payload.pipeline

    # Bundle mode is keyed on *presence* of the field, not truthiness
    # — the contract is "bundle if the caller chose to send it",
    # which an empty dict still expresses (no agents to remap, but
    # the user explicitly opted in). Keeps the contract documented
    # in :class:`PipelineImportRequest` accurate.
    if bundled is not None:
        # Step 1 — create bundled agents (no inter-agent ordering
        # required today). Build the remap from source ids to
        # fresh local ids.
        id_remap: dict[str, str] = {}
        for source_id, agent_payload in bundled.items():
            try:
                agent_create = AgentCreate.model_validate(agent_payload.model_dump())
            except ValidationError as exc:
                # Keep the structured Pydantic error list intact
                # under ``agent_validation_errors`` so frontends can
                # surface field-level issues. ``errors`` keeps the
                # human-friendly summary the rest of the import
                # paths use, including the offending source id so
                # operators know which entry in the bundle broke.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "errors": [
                            f"bundled_agents['{source_id}'] is not a valid agent payload",
                        ],
                        "warnings": [],
                        "agent_validation_errors": exc.errors(),
                        "source_id": source_id,
                    },
                ) from exc
            new_agent = repo.create_agent(session, agent_create, user_id=user.id)
            id_remap[source_id] = new_agent.id

        # Step 2 — rewrite node.agent_id in the pipeline payload.
        # Nodes whose agent_id isn't in the bundle keep their
        # original string; the validator will catch them if they
        # don't exist locally either (legitimate use case: a
        # partial bundle that relies on some pre-existing agents).
        rewritten_nodes = [
            node.model_copy(
                update={"agent_id": id_remap.get(node.agent_id, node.agent_id)},
            )
            for node in pipeline_payload.nodes
        ]
        pipeline_payload = pipeline_payload.model_copy(update={"nodes": rewritten_nodes})

    try:
        create_payload = PipelineCreate.model_validate(pipeline_payload.model_dump())
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    # ``_enforce_validation`` raises 422 on any DAG / cohesion
    # error. The session-wide rollback in ``get_session`` then
    # tears down the bundled agents created above — no orphans.
    _enforce_validation(create_payload, session)
    return repo.create_pipeline(session, create_payload, user_id=user.id)


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    response_model=Pipeline,
)
def import_pipeline(
    payload: PipelineImportRequest,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    """Create a new pipeline (v1) from an exported JSON payload (#124).

    Pure file-upload path: the caller embeds the entire bundle JSON
    in the request body. For the URL-based registry flow see
    ``POST /pipelines/import-from-url``.
    """
    return _materialise_pipeline_import(payload, session, user)


# ---------------------------------------------------------------------------
# Private template registry — import from URL (#385)
# ---------------------------------------------------------------------------


# Bundles bigger than this are almost certainly attempting to DoS the
# import path; a real ``.pipeline-bundle.json`` (5-node pipeline, 5
# bundled agents with full prompt templates) sits around 50-100 KB.
_BUNDLE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BUNDLE_FETCH_TIMEOUT_SECONDS = 10.0


class ImportFromUrlRequest(BaseModel):
    """Body of ``POST /pipelines/import-from-url`` (#385)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        description=(
            "Absolute URL of a ``.pipeline-bundle.json`` file. Hostname "
            "must match an entry in ``DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS`` "
            "(set on the engine). HTTPS required except for ``localhost`` "
            "/ ``127.0.0.1`` (dev only)."
        )
    )


def _validate_registry_url(url: str, allowed_hosts: list[str]) -> str:
    """Reject URLs that aren't on the operator-configured allow-list.

    Returns the parsed hostname (caller logs it). Raises HTTPException
    with the precise reason on the first violation — operators get a
    clear 422 instead of a confusing downstream timeout / DNS error.
    """
    if not allowed_hosts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "import-from-url is disabled. Set "
                "DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS on the engine to a "
                "CSV of trusted hostnames (e.g. "
                "raw.githubusercontent.com,gitlab.internal.com)."
            ),
        )

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"malformed url: {exc}",
        ) from exc

    if parsed.scheme not in {"https", "http"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"url scheme must be https (got {parsed.scheme!r})",
        )

    # Userinfo (``https://user:pass@host/...``) is rejected outright:
    # the URL is persisted to the audit log + may appear in error
    # responses, so accepting credentials in the URL would leak them.
    # Operators authenticate via the engine-side
    # ``DAP_TEMPLATE_REGISTRY_AUTH_TOKEN`` env var instead.
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "url must not contain userinfo (user:pass@). "
                "Use DAP_TEMPLATE_REGISTRY_AUTH_TOKEN for authentication."
            ),
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="url has no hostname",
        )

    # Block link-local + cloud metadata + multicast bypass attempts
    # even if the operator accidentally allow-lists ``0.0.0.0``. The
    # only legitimate loopback use is dev with an explicit
    # ``localhost`` / ``127.0.0.1`` entry on the allow-list.
    _LOOPBACK_BYPASS = {"0.0.0.0", "169.254.169.254"}
    if hostname in _LOOPBACK_BYPASS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"hostname {hostname!r} is blocked (SSRF guard)",
        )

    # ``localhost`` / ``127.0.0.1`` accept either http or https when
    # explicitly on the allow-list — useful for testing against a
    # local registry mock where HTTPS isn't always set up. Every
    # other host MUST be https; plain http to a remote registry is
    # rejected even if the host is allow-listed.
    if hostname not in {"localhost", "127.0.0.1"} and parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "url scheme must be https for remote hosts "
                "(http is allowed only for localhost/127.0.0.1 in dev)"
            ),
        )

    if hostname not in {h.lower() for h in allowed_hosts}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"hostname {hostname!r} not in allow-list (DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS)"
            ),
        )

    return hostname


@router.post(
    "/import-from-url",
    status_code=status.HTTP_201_CREATED,
    response_model=Pipeline,
)
def import_pipeline_from_url(
    payload: ImportFromUrlRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    """Fetch a ``.pipeline-bundle.json`` from a trusted URL + import (#385).

    Iter 1 of the private template registry. The endpoint:

    1. Validates the URL against ``DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS``.
    2. GETs the bundle with a 10s timeout and a 10MB response cap.
    3. Optionally attaches ``Authorization: Bearer
       $DAP_TEMPLATE_REGISTRY_AUTH_TOKEN`` if the engine is configured
       with a token (private GitHub/GitLab repos).
    4. Parses the response as ``PipelineImportRequest`` and delegates
       to the shared import path.
    5. Writes a ``pipeline.imported_from_url`` audit event with the
       source URL and resulting pipeline id, so operators have an
       auditable trail of *where* each pipeline came from.

    Trust model: only operators with write access to the engine's
    config can add hosts to the allow-list, so a misbehaving user
    can't redirect the engine at an internal SSRF target.
    """
    cfg = request.app.state.config
    allowed_hosts: list[str] = cfg.template_registry_allowed_hosts or []
    auth_token: str | None = cfg.template_registry_auth_token

    hostname = _validate_registry_url(payload.url, allowed_hosts)

    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    # Stream the body in chunks rather than buffering everything into
    # ``response.content`` first — a malicious / buggy upstream could
    # otherwise burn unbounded bandwidth + memory before the size
    # check fires. We also short-circuit on ``Content-Length`` when
    # it's set and already over the cap, so we don't even start the
    # transfer.
    try:
        with (
            httpx.Client(timeout=_BUNDLE_FETCH_TIMEOUT_SECONDS) as client,
            client.stream("GET", payload.url, headers=headers, follow_redirects=False) as response,
        ):
            if response.status_code != 200:  # noqa: PLR2004 — HTTP 200 is the protocol contract, not a magic value
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"bundle URL returned HTTP {response.status_code}",
                )

            advertised = response.headers.get("content-length")
            if advertised is not None:
                try:
                    advertised_bytes = int(advertised)
                except ValueError:
                    advertised_bytes = -1
                if advertised_bytes > _BUNDLE_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"bundle Content-Length is {advertised_bytes} bytes — "
                            f"exceeds {_BUNDLE_MAX_BYTES} byte limit"
                        ),
                    )

            # Read in 64 KB chunks. Abort the moment we cross the
            # cap so we never materialise an arbitrarily large
            # response in memory.
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > _BUNDLE_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"bundle exceeds {_BUNDLE_MAX_BYTES} byte limit "
                            f"(aborted streaming at {total} bytes)"
                        ),
                    )
                chunks.append(chunk)
            raw_body = b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"timeout fetching bundle from {hostname!r} after "
                f"{_BUNDLE_FETCH_TIMEOUT_SECONDS:.0f}s"
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"http error fetching bundle: {exc}",
        ) from exc

    try:
        import_request = PipelineImportRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        # ``exc.errors()`` for ``json_invalid`` errors carries the raw
        # body as ``input: bytes``, which FastAPI's JSON serialiser
        # can't render. Strip the raw input + keep only the human-
        # readable summary so the response stays JSON-safe.
        sanitized = [
            {"type": err["type"], "loc": list(err["loc"]), "msg": err["msg"]}
            for err in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"errors": ["bundle JSON failed validation"], "details": sanitized},
        ) from exc

    pipeline = _materialise_pipeline_import(import_request, session, user)

    # Audit trail — operators need to know *where* this pipeline came
    # from later. We log the URL (operator already controls the
    # allow-list so the URL isn't sensitive) + the resulting pipeline id.
    try:
        record_audit_event(
            session,
            user_id=user.id,
            event_type="pipeline.imported_from_url",
            event_data={"url": payload.url, "pipeline_id": pipeline.id},
        )
    except Exception:
        # Audit-log failures shouldn't block a successful import; we
        # already paid the network + bundle materialisation cost. The
        # repo layer logs the underlying error.
        logger.exception("audit-log write failed for imported pipeline %s", pipeline.id)

    return pipeline


@router.get("/{pipeline_id}", response_model=Pipeline)
def get_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    try:
        return repo.get_pipeline(session, pipeline_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{pipeline_id}", response_model=Pipeline)
def update_pipeline(
    pipeline_id: str,
    payload: PipelineUpdate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    """Update a pipeline (creates a new version). Rejects invalid DAGs with 422 (#120).

    Same enforcement as ``POST /pipelines`` — a failed update never
    rolls a new version. Existing pipeline keeps its current version
    when validation fails.

    Existence is checked **before** the validator: an unknown
    ``pipeline_id`` should always return 404 regardless of whether
    the body would also fail DAG validation. Otherwise a typo in
    the URL plus a typo in the body would surface as 422 and hide
    the real "this id doesn't exist" cause.

    Ownership gates are inside ``repo.get_pipeline`` /
    ``repo.update_pipeline``: a non-admin probing someone else's
    pipeline_id gets 404, never 422 — the validator never runs on a
    foreign id, so the body shape can't leak through error details.
    """
    try:
        repo.get_pipeline(session, pipeline_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _enforce_validation(_update_to_create_shape(payload), session)
    try:
        return repo.update_pipeline(
            session, pipeline_id, payload, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Response:
    try:
        repo.archive_pipeline(session, pipeline_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{pipeline_id}/versions", response_model=list[Pipeline])
def list_pipeline_versions(
    pipeline_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> list[Pipeline]:
    try:
        return repo.list_pipeline_versions(
            session, pipeline_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{pipeline_id}/versions/{version}", response_model=Pipeline)
def get_pipeline_version(
    pipeline_id: str,
    version: int,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Pipeline:
    try:
        return repo.get_pipeline_version(
            session, pipeline_id, version, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{pipeline_id}/export",
    response_model=PipelineExport,
    # ``bundled_agents`` defaults to ``None`` on non-bundle exports;
    # ``exclude_none`` strips it from the JSON so Phase 1 wire format
    # stays exactly the way it was — no ``"bundled_agents": null``
    # noise in pipeline-only exports, and the field appears only when
    # the operator opted in via ``?bundle=true``.
    response_model_exclude_none=True,
)
def export_pipeline(
    pipeline_id: str,
    bundle: bool = Query(
        default=False,
        description=(
            "When true, include every agent the pipeline references in "
            "``bundled_agents`` so a target installation that doesn't "
            "have them yet can import the whole thing in one shot."
        ),
    ),
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> PipelineExport:
    """Return a portable JSON shape of the pipeline (#124, #126).

    Strips per-installation fields (id, version, timestamps,
    archived_at) so the result can move between DAP installations
    or be checked into git. ``node.agent_id`` strings still point
    at the *source* installation's agent ids.

    With ``bundle=true`` (#126) the response also carries a
    ``bundled_agents`` map keyed by source agent id. The importer
    creates each agent, builds an ``old_id → new_id`` remap, and
    rewrites every ``node.agent_id`` before persisting the
    pipeline. Without the bundle, a target DB that's missing any
    referenced agent gets the standard 422 from the DAG validator.
    """
    try:
        pipeline = repo.get_pipeline(
            session, pipeline_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        payload = PipelineExportPayload(
            name=pipeline.name,
            description=pipeline.description,
            schema_version=pipeline.schema_version,
            state_schema_ref=pipeline.state_schema_ref,
            entry_point=pipeline.entry_point,
            nodes=list(pipeline.nodes),
            edges=list(pipeline.edges),
            defaults=pipeline.defaults,
            ui_metadata=pipeline.ui_metadata,
        )
    except ValidationError as exc:
        # Shouldn't happen — stored shape was validated on write —
        # but if a future migration ever introduces a stored shape
        # we can't export, surface it as 500 with details rather
        # than crashing without context.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored pipeline could not be re-validated for export: {exc.errors()}",
        ) from exc

    bundled_agents: dict[str, AgentExportPayload] | None = None
    if bundle:
        # One batched fetch for every unique agent_id in the pipeline
        # — a 2-query lookup regardless of node count (#126 review).
        # ``_check_agents`` at save time guarantees a valid pipeline
        # has no archived references, so any id we don't find here
        # would be a row that vanished after save (race / manual DB
        # tinkering). Skip silently in that case; the importer falls
        # back to "agent not found" 422 on the receiving end if the
        # bundle ends up incomplete.
        agent_ids = sorted({node.agent_id for node in payload.nodes})
        agents_by_id = repo.get_agents_by_ids(
            session, agent_ids, actor_id=user.id, is_admin=user.is_superuser
        )
        bundled_agents = {
            agent_id: build_agent_export_payload(agents_by_id[agent_id])
            for agent_id in agent_ids
            if agent_id in agents_by_id
        }

    return PipelineExport(
        schema_version=PIPELINE_EXPORT_SCHEMA_VERSION,
        min_dap_version=__version__,
        pipeline=payload,
        bundled_agents=bundled_agents,
    )

"""REST CRUD for pipelines."""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from dap_types import Pipeline
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from dap_engine.api.backend_profiles import inspect_backend_profiles
from dap_engine.api.deps import get_session
from dap_engine.api.pipeline_bundles import (
    PipelineBundleError,
    build_pipeline_export,
    fetch_pipeline_import_request_from_url,
    materialise_pipeline_import,
)
from dap_engine.api.schemas import (
    BackendProfilesInspectionResponse,
    PipelineExport,
    PipelineImportRequest,
)
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.contracts import PipelineCreate, PipelineUiMetadataPatch, PipelineUpdate
from dap_engine.execution import ValidationResult, validate_pipeline_dag
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

logger = logging.getLogger("dap.engine.pipelines")

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _raise_bundle_http_error(exc: PipelineBundleError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
        "has_more": offset + limit < total,
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
    try:
        return materialise_pipeline_import(payload, session, user)
    except PipelineBundleError as exc:
        _raise_bundle_http_error(exc)


@router.post(
    "/import/inspect-backends",
    response_model=BackendProfilesInspectionResponse,
)
def inspect_pipeline_import_backends(
    payload: PipelineImportRequest,
    session: Session = Depends(get_session),
    _user: UserORM = Depends(current_active_user),
) -> BackendProfilesInspectionResponse:
    """Inspect backend profiles declared by a pipeline bundle.

    This is the read-only preflight endpoint for the dashboard's import
    configuration step. It accepts the same bundle envelope as
    ``POST /pipelines/import`` and returns only non-secret availability
    metadata; the actual import path remains unchanged.
    """
    return inspect_backend_profiles(payload, session)


# ---------------------------------------------------------------------------
# Private template registry — import from URL (#385)
# ---------------------------------------------------------------------------


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

    try:
        fetched = fetch_pipeline_import_request_from_url(
            payload.url,
            allowed_hosts=allowed_hosts,
            auth_token=auth_token,
        )
        pipeline = materialise_pipeline_import(fetched.import_request, session, user)
    except PipelineBundleError as exc:
        _raise_bundle_http_error(exc)

    # Audit trail — operators need to know *where* this pipeline came
    # from later. Query strings/fragments are stripped by the fetch
    # helper before audit so accidental URL tokens don't land in logs.
    try:
        record_audit_event(
            session,
            user_id=user.id,
            event_type="pipeline.imported_from_url",
            event_data={"url": fetched.audit_url, "pipeline_id": pipeline.id},
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


@router.patch("/{pipeline_id}/ui-metadata", status_code=status.HTTP_204_NO_CONTENT)
def update_pipeline_ui_metadata(
    pipeline_id: str,
    payload: PipelineUiMetadataPatch,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Response:
    """Merge dashboard layout metadata into the current version.

    Unlike ``PUT /pipelines/{id}``, this is intentionally versionless:
    drag/pan autosave should not create v2/v3 history rows.
    """
    try:
        repo.update_pipeline_ui_metadata(
            session,
            pipeline_id,
            payload,
            actor_id=user.id,
            is_admin=user.is_superuser,
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
        return build_pipeline_export(
            session,
            pipeline_id,
            actor_id=user.id,
            is_admin=user.is_superuser,
            bundle=bundle,
        )
    except PipelineBundleError as exc:
        _raise_bundle_http_error(exc)

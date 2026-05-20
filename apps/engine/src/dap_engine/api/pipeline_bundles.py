"""Pipeline bundle import/export service helpers.

This module owns bundle materialisation, portable export payload construction,
and trusted-registry fetching. API routers keep HTTP dependencies, auth, and
audit placement; this service raises ``PipelineBundleError`` with route-ready
status/detail payloads so existing wire behavior stays stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlparse
from uuid import UUID

import httpx
from dap_types import Agent, Pipeline
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.export_redaction import scrub_secret_like_keys
from dap_engine.api.schemas import (
    PIPELINE_EXPORT_SCHEMA_VERSION,
    AgentExportPayload,
    PipelineExport,
    PipelineExportPayload,
    PipelineImportRequest,
)
from dap_engine.contracts import AgentCreate, PipelineCreate
from dap_engine.execution import validate_pipeline_dag
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM
from dap_engine.version import __version__

BundleDetail = str | dict[str, Any] | list[Any]

_BUNDLE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BUNDLE_FETCH_TIMEOUT_SECONDS = 10.0


class PipelineBundleError(Exception):
    """Route-ready bundle error preserving existing HTTP response shapes."""

    def __init__(self, status_code: int, detail: BundleDetail) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class FetchedPipelineBundle:
    """Parsed bundle fetched from a trusted template registry."""

    import_request: PipelineImportRequest
    audit_url: str


def _raise(status_code: int, detail: BundleDetail) -> NoReturn:
    raise PipelineBundleError(status_code, detail)


def _enforce_min_dap_version(payload: PipelineImportRequest) -> None:
    """Reject bundles that require a newer DAP engine than this instance."""
    required = payload.min_dap_version
    if required is None:
        return

    try:
        required_version = Version(required)
    except InvalidVersion as exc:
        raise PipelineBundleError(
            422,
            (
                "Invalid min_dap_version in pipeline bundle. Expected a "
                f"valid version like 'MAJOR.MINOR.PATCH' or 'MAJOR.MINOR.PATCH-rc1', "
                f"got '{required}'."
            ),
        ) from exc

    current_version = Version(__version__)
    if current_version < required_version:
        _raise(
            422,
            (
                f"Bundle requires DAP >= {required}, but this instance is {__version__}. "
                "Update DAP before importing this bundle."
            ),
        )


def _enforce_validation(payload: PipelineCreate, session: Session) -> None:
    """Run DAG validation and preserve the router's existing 422 shape."""
    result = validate_pipeline_dag(payload, session)
    if not result.valid:
        _raise(422, {"errors": result.errors, "warnings": result.warnings})


def _build_bundled_agent_export_payload(agent: Agent) -> AgentExportPayload:
    """Project an agent onto the portable bundle export shape."""
    try:
        return AgentExportPayload(
            name=agent.name,
            role=agent.role,
            runtime_id=agent.runtime_id,
            runtime_config=scrub_secret_like_keys(agent.runtime_config),
            prompt_template=agent.prompt_template,
            input_schema=list(agent.input_schema),
            output_schema=list(agent.output_schema),
            constraints=list(agent.constraints),
            budget_limit_usd=agent.budget_limit_usd,
            timeout_ms=agent.timeout_ms,
        )
    except ValidationError as exc:
        raise PipelineBundleError(
            500,
            f"Stored bundled agent could not be re-validated for export: {exc.errors()}",
        ) from exc


def materialise_pipeline_import(
    payload: PipelineImportRequest,
    session: Session,
    user: UserORM,
) -> Pipeline:
    """Create a new pipeline from a parsed import payload.

    Supports pipeline-only imports and bundled-agent imports. In bundle mode,
    creates every bundled agent first, remaps source agent ids to local ids, then
    validates and persists the pipeline. The caller owns the SQLAlchemy session,
    so any validation failure rolls back newly created bundled agents.
    """
    _enforce_min_dap_version(payload)

    bundled = payload.bundled_agents
    pipeline_payload = payload.pipeline

    if bundled is not None:
        id_remap: dict[str, str] = {}
        for source_id, agent_payload in bundled.items():
            # BundledAgentImportPayload already validated this relaxed bundle
            # shape, including extension fields. Re-validating through
            # AgentCreate would reject supported bundle extension fields.
            agent_create = AgentCreate.model_construct(
                name=agent_payload.name,
                role=agent_payload.role,
                runtime_id=agent_payload.runtime_id,
                runtime_config=agent_payload.runtime_config,
                prompt_template=agent_payload.prompt_template,
                input_schema=agent_payload.input_schema,
                output_schema=agent_payload.output_schema,
                constraints=agent_payload.constraints,
                budget_limit_usd=agent_payload.budget_limit_usd,
                timeout_ms=agent_payload.timeout_ms,
            )
            new_agent = repo.create_agent(session, agent_create, user_id=user.id)
            id_remap[source_id] = new_agent.id

        rewritten_nodes = [
            node.model_copy(update={"agent_id": id_remap.get(node.agent_id, node.agent_id)})
            for node in pipeline_payload.nodes
        ]
        pipeline_payload = pipeline_payload.model_copy(update={"nodes": rewritten_nodes})

    try:
        create_payload = PipelineCreate.model_validate(
            {**pipeline_payload.model_dump(), "backend_profiles": payload.backend_profiles}
        )
    except ValidationError as exc:
        raise PipelineBundleError(422, exc.errors()) from exc

    _enforce_validation(create_payload, session)
    return repo.create_pipeline(session, create_payload, user_id=user.id)


def build_pipeline_export(
    session: Session,
    pipeline_id: str,
    *,
    actor_id: UUID,
    is_admin: bool,
    bundle: bool,
) -> PipelineExport:
    """Build the portable pipeline export envelope."""
    try:
        pipeline = repo.get_pipeline(session, pipeline_id, actor_id=actor_id, is_admin=is_admin)
    except repo.NotFoundError as exc:
        raise PipelineBundleError(404, str(exc)) from exc

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
        raise PipelineBundleError(
            500,
            f"Stored pipeline could not be re-validated for export: {exc.errors()}",
        ) from exc

    bundled_agents: dict[str, AgentExportPayload] | None = None
    if bundle:
        agent_ids = sorted({node.agent_id for node in payload.nodes})
        agents_by_id = repo.get_agents_by_ids(
            session, agent_ids, actor_id=actor_id, is_admin=is_admin
        )
        bundled_agents = {
            agent_id: _build_bundled_agent_export_payload(agents_by_id[agent_id])
            for agent_id in agent_ids
            if agent_id in agents_by_id
        }

    return PipelineExport(
        schema_version=PIPELINE_EXPORT_SCHEMA_VERSION,
        min_dap_version=__version__,
        pipeline=payload,
        bundled_agents=bundled_agents,
    )


def validate_registry_url(url: str, allowed_hosts: list[str]) -> str:
    """Reject URLs that are not on the operator-configured allow-list."""
    if not allowed_hosts:
        _raise(
            422,
            (
                "import-from-url is disabled. Set "
                "DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS on the engine to a "
                "CSV of trusted hostnames (e.g. "
                "raw.githubusercontent.com,gitlab.internal.com)."
            ),
        )

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise PipelineBundleError(422, f"malformed url: {exc}") from exc

    if parsed.scheme not in {"https", "http"}:
        _raise(422, f"url scheme must be https (got {parsed.scheme!r})")

    if parsed.username or parsed.password:
        _raise(
            422,
            (
                "url must not contain userinfo (user:pass@). "
                "Use DAP_TEMPLATE_REGISTRY_AUTH_TOKEN for authentication."
            ),
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        _raise(422, "url has no hostname")

    if hostname in {"0.0.0.0", "169.254.169.254"}:
        _raise(422, f"hostname {hostname!r} is blocked (SSRF guard)")

    if hostname not in {"localhost", "127.0.0.1"} and parsed.scheme != "https":
        _raise(
            422,
            (
                "url scheme must be https for remote hosts "
                "(http is allowed only for localhost/127.0.0.1 in dev)"
            ),
        )

    if hostname not in {h.lower() for h in allowed_hosts}:
        _raise(
            422,
            f"hostname {hostname!r} not in allow-list (DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS)",
        )

    return hostname


def _sanitize_url_for_audit(url: str) -> str:
    """Drop query/fragment data before writing a user-supplied URL to audit."""
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def fetch_pipeline_import_request_from_url(
    url: str,
    *,
    allowed_hosts: list[str],
    auth_token: str | None,
) -> FetchedPipelineBundle:
    """Fetch and parse a bundle from a trusted template-registry URL."""
    hostname = validate_registry_url(url, allowed_hosts)

    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        with (
            httpx.Client(timeout=_BUNDLE_FETCH_TIMEOUT_SECONDS) as client,
            client.stream("GET", url, headers=headers, follow_redirects=False) as response,
        ):
            if response.status_code != 200:  # noqa: PLR2004
                _raise(502, f"bundle URL returned HTTP {response.status_code}")

            advertised = response.headers.get("content-length")
            if advertised is not None:
                try:
                    advertised_bytes = int(advertised)
                except ValueError:
                    advertised_bytes = -1
                if advertised_bytes > _BUNDLE_MAX_BYTES:
                    _raise(
                        413,
                        (
                            f"bundle Content-Length is {advertised_bytes} bytes — "
                            f"exceeds {_BUNDLE_MAX_BYTES} byte limit"
                        ),
                    )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > _BUNDLE_MAX_BYTES:
                    _raise(
                        413,
                        (
                            f"bundle exceeds {_BUNDLE_MAX_BYTES} byte limit "
                            f"(aborted streaming at {total} bytes)"
                        ),
                    )
                chunks.append(chunk)
            raw_body = b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise PipelineBundleError(
            502,
            f"timeout fetching bundle from {hostname!r} after {_BUNDLE_FETCH_TIMEOUT_SECONDS:.0f}s",
        ) from exc
    except httpx.HTTPError as exc:
        raise PipelineBundleError(502, f"http error fetching bundle: {exc}") from exc

    try:
        import_request = PipelineImportRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        sanitized = [
            {"type": err["type"], "loc": list(err["loc"]), "msg": err["msg"]}
            for err in exc.errors()
        ]
        raise PipelineBundleError(
            422,
            {"errors": ["bundle JSON failed validation"], "details": sanitized},
        ) from exc

    return FetchedPipelineBundle(
        import_request=import_request,
        audit_url=_sanitize_url_for_audit(url),
    )

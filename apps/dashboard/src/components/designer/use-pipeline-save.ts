"use client";

/**
 * Save + validate flow for the designer (audit D1 split).
 *
 * Bundles together the three server mutations (validate / create /
 * update) and the ``buildPayload`` projection that feeds them. The
 * orchestrator only sees handlers (``handleValidate`` /
 * ``handleSave``) plus the surface state the toolbar renders
 * (``isSaving``, ``submitError``, ``saveLabel``).
 *
 * Edit-vs-create dispatch happens inside ``handleSave``: a non-null
 * ``initialPipeline`` routes through ``update``, null routes
 * through ``create``. Both push the user to the edit page for the
 * resulting pipeline ID on success.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Viewport } from "@xyflow/react";

import {
  useCreatePipeline,
  useUpdatePipeline,
  useUpdatePipelineUiMetadata,
  useValidatePipeline,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type {
  Pipeline,
  PipelineEdge,
  PipelineNode,
  ValidationResult,
} from "@/lib/api/types";

import {
  DEFAULT_DEFAULTS,
  STATE_SCHEMA_REF,
  type PipelineFormPayload,
} from "./types";
import type { EdgeWaypoints } from "./reactflow-adapters";

const AUTOSAVE_LAYOUT_DEBOUNCE_MS = 500;

export type AutoSaveLayoutStatus = "idle" | "saving" | "saved" | "error";

interface BuildPipelinePayloadParams {
  name: string;
  description: string;
  entryPoint: string;
  designerNodes: PipelineNode[];
  designerEdges: PipelineEdge[];
  initialPipeline: Pipeline | null;
  viewport?: Viewport | null;
  edgeWaypoints?: EdgeWaypoints;
  /** Per-node LLM/backend assignments (#755 follow-up). Sent verbatim so the
   *  engine resolver and a future re-import see the same shape. */
  backendProfiles?: Record<string, unknown> | null;
}

interface BuildLayoutUiMetadataParams {
  designerNodes: PipelineNode[];
  existingUiMetadata?: Record<string, unknown> | null;
  viewport?: Viewport | null;
  edgeWaypoints?: EdgeWaypoints;
}

function existingMetadata(pipeline: Pipeline | null): Record<string, unknown> {
  return pipeline?.ui_metadata && typeof pipeline.ui_metadata === "object"
    ? (pipeline.ui_metadata as Record<string, unknown>)
    : {};
}

function isValidViewport(viewport: Viewport | null | undefined): viewport is Viewport {
  return (
    viewport != null &&
    Number.isFinite(viewport.x) &&
    Number.isFinite(viewport.y) &&
    Number.isFinite(viewport.zoom) &&
    viewport.zoom >= 0.1 &&
    viewport.zoom <= 4
  );
}

export function buildLayoutUiMetadata({
  designerNodes,
  existingUiMetadata = {},
  viewport,
  edgeWaypoints,
}: BuildLayoutUiMetadataParams): Record<string, unknown> {
  const nodePositions: Record<string, { x: number; y: number }> = {};
  for (const n of designerNodes) {
    nodePositions[n.id] = { x: n.position.x, y: n.position.y };
  }

  return {
    ...(existingUiMetadata ?? {}),
    node_positions: nodePositions,
    ...(isValidViewport(viewport) ? { viewport } : {}),
    ...(edgeWaypoints ? { edge_waypoints: edgeWaypoints } : {}),
  };
}

export function buildPipelinePayload({
  name,
  description,
  entryPoint,
  designerNodes,
  designerEdges,
  initialPipeline,
  viewport,
  edgeWaypoints,
  backendProfiles,
}: BuildPipelinePayloadParams): PipelineFormPayload {
  return {
    name,
    description,
    schema_version: "langgraph/1.0",
    state_schema_ref: STATE_SCHEMA_REF,
    entry_point: entryPoint,
    nodes: designerNodes,
    edges: designerEdges,
    defaults: DEFAULT_DEFAULTS,
    ui_metadata: buildLayoutUiMetadata({
      designerNodes,
      existingUiMetadata: existingMetadata(initialPipeline),
      viewport,
      edgeWaypoints,
    }),
    // Carry assignments through every save so a layout-only save doesn't drop
    // them. Falls back to the pipeline's existing profiles when unmanaged.
    backend_profiles:
      backendProfiles ??
      (initialPipeline?.backend_profiles as Record<string, unknown> | undefined) ??
      null,
  };
}

type UsePipelineSaveParams = BuildPipelinePayloadParams;

interface UsePipelineSaveResult {
  handleValidate: () => Promise<void>;
  handleSave: () => Promise<void>;
  validationResult: ValidationResult | null;
  isValidating: boolean;
  isSaving: boolean;
  saveLabel: string;
  submitError: unknown;
}


export function usePipelineSave({
  name,
  description,
  entryPoint,
  designerNodes,
  designerEdges,
  initialPipeline,
  viewport,
  edgeWaypoints,
}: UsePipelineSaveParams): UsePipelineSaveResult {
  const router = useRouter();
  const validate = useValidatePipeline();
  const create = useCreatePipeline();
  const update = useUpdatePipeline();
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  const buildPayload = useCallback(
    (): PipelineFormPayload =>
      buildPipelinePayload({
        name,
        description,
        entryPoint,
        designerNodes,
        designerEdges,
        initialPipeline,
        viewport,
        edgeWaypoints,
      }),
    [
      name,
      description,
      entryPoint,
      designerNodes,
      designerEdges,
      initialPipeline,
      viewport,
      edgeWaypoints,
    ],
  );

  const handleValidate = useCallback(async () => {
    // Clear any previous result first — otherwise a stale "valid" sticks
    // around if the next validation attempt fails (and the Save button
    // would stay enabled based on the old success state).
    setValidationResult(null);
    try {
      const result = await validate.mutateAsync(buildPayload());
      setValidationResult(result);
    } catch {
      // The mutation's error state already drives the UI via submitError;
      // swallow here to avoid an unhandled rejection in the dev overlay.
    }
  }, [buildPayload, validate]);

  const handleSave = useCallback(async () => {
    const payload = buildPayload();
    try {
      if (initialPipeline) {
        const updated = await update.mutateAsync({
          id: initialPipeline.id,
          payload,
        });
        router.push(`/pipelines/${updated.id}/edit`);
      } else {
        const created = await create.mutateAsync(payload);
        router.push(`/pipelines/${created.id}/edit`);
      }
    } catch {
      // Save failure surfaces via create.error / update.error on the
      // toolbar; swallow here to avoid the unhandled rejection.
    }
  }, [buildPayload, initialPipeline, create, update, router]);

  const isSaving = create.isPending || update.isPending;
  const saveLabel = initialPipeline ? `Save v${initialPipeline.version + 1}` : "Save";
  // Surface server-side errors (422 from validate / save) in the toolbar
  // so the user sees the actual reason instead of just a dev-overlay flash.
  const submitError = create.error ?? update.error ?? validate.error;

  return {
    handleValidate,
    handleSave,
    validationResult,
    isValidating: validate.isPending,
    isSaving,
    saveLabel,
    submitError,
  };
}

interface UseAutoSaveLayoutParams {
  pipelineId: string | null;
  designerNodes: PipelineNode[];
  existingUiMetadata?: Record<string, unknown> | null;
  viewport?: Viewport | null;
  edgeWaypoints?: EdgeWaypoints;
}

interface UseAutoSaveLayoutResult {
  status: AutoSaveLayoutStatus;
  savedAt: Date | null;
  error: string | null;
}

export function useAutoSaveLayout({
  pipelineId,
  designerNodes,
  existingUiMetadata,
  viewport,
  edgeWaypoints,
}: UseAutoSaveLayoutParams): UseAutoSaveLayoutResult {
  const { mutateAsync } = useUpdatePipelineUiMetadata();
  const [status, setStatus] = useState<AutoSaveLayoutStatus>("idle");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [currentError, setCurrentError] = useState<string | null>(null);
  const lastSnapshotRef = useRef<string | null>(null);
  const requestSeqRef = useRef(0);

  const uiMetadata = useMemo(
    () =>
      buildLayoutUiMetadata({
        designerNodes,
        existingUiMetadata,
        viewport,
        edgeWaypoints,
      }),
    [designerNodes, existingUiMetadata, viewport, edgeWaypoints],
  );

  useEffect(() => {
    if (!pipelineId) return;

    const snapshot = JSON.stringify(uiMetadata);
    if (lastSnapshotRef.current === null) {
      lastSnapshotRef.current = snapshot;
      return;
    }
    if (lastSnapshotRef.current === snapshot) return;

    const timeout = window.setTimeout(() => {
      const requestSeq = requestSeqRef.current + 1;
      requestSeqRef.current = requestSeq;
      setStatus("saving");
      setCurrentError(null);
      mutateAsync({ id: pipelineId, payload: { ui_metadata: uiMetadata } })
        .then(() => {
          if (requestSeq !== requestSeqRef.current) return;
          lastSnapshotRef.current = snapshot;
          setSavedAt(new Date());
          setCurrentError(null);
          setStatus("saved");
        })
        .catch((nextError: unknown) => {
          if (requestSeq !== requestSeqRef.current) return;
          setCurrentError(formatApiError(nextError));
          setStatus("error");
        });
    }, AUTOSAVE_LAYOUT_DEBOUNCE_MS);

    return () => window.clearTimeout(timeout);
  }, [pipelineId, uiMetadata, mutateAsync]);

  return { status, savedAt, error: currentError };
}

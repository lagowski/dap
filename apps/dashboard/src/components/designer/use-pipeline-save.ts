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

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import {
  useCreatePipeline,
  useUpdatePipeline,
  useValidatePipeline,
} from "@/hooks/api";
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


interface UsePipelineSaveParams {
  name: string;
  description: string;
  entryPoint: string;
  designerNodes: PipelineNode[];
  designerEdges: PipelineEdge[];
  initialPipeline: Pipeline | null;
}


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
}: UsePipelineSaveParams): UsePipelineSaveResult {
  const router = useRouter();
  const validate = useValidatePipeline();
  const create = useCreatePipeline();
  const update = useUpdatePipeline();
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  const buildPayload = useCallback((): PipelineFormPayload => {
    // Snapshot current node positions into ui_metadata so they survive
    // page refresh. PipelineNode.position is the authoritative storage;
    // node_positions here is an explicit UI-layer copy that the designer
    // applies on load before ReactFlow's fitView can shift things (#226).
    const nodePositions: Record<string, { x: number; y: number }> = {};
    for (const n of designerNodes) {
      nodePositions[n.id] = { x: n.position.x, y: n.position.y };
    }
    const existingMeta =
      initialPipeline?.ui_metadata &&
      typeof initialPipeline.ui_metadata === "object"
        ? (initialPipeline.ui_metadata as Record<string, unknown>)
        : {};
    return {
      name,
      description,
      schema_version: "langgraph/1.0",
      state_schema_ref: STATE_SCHEMA_REF,
      entry_point: entryPoint,
      nodes: designerNodes,
      edges: designerEdges,
      defaults: DEFAULT_DEFAULTS,
      ui_metadata: { ...existingMeta, node_positions: nodePositions },
    };
  }, [name, description, entryPoint, designerNodes, designerEdges, initialPipeline]);

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

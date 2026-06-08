"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Trash2 } from "lucide-react";
import {
  addProfile,
  getDefaultProfile,
  listProfiles,
  profileSummary,
  removeProfile,
  resolveNodeProfile,
  setDefaultProfile,
  setNodeOverride,
  type BackendProfiles,
} from "@/lib/backend-profile-assignments";

export interface NodeRef {
  id: string;
  label: string;
}

interface ProfileSelectProps {
  value: string; // "" = inherit default
  profiles: { id: string; label: string }[];
  defaultLabel: string;
  onChange: (profileId: string | null) => void;
  ariaLabel: string;
}

/** Native select: "Inherit default" + every available profile. */
function ProfileSelect({ value, profiles, defaultLabel, onChange, ariaLabel }: ProfileSelectProps) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value || null)}
      className="w-full rounded-md border bg-background px-2 py-1 text-xs outline-none focus:ring-1 focus:ring-ring"
    >
      <option value="">Inherit default ({defaultLabel})</option>
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.label}
        </option>
      ))}
    </select>
  );
}

/** Per-node LLM/backend assignment dropdown — used inline in the node panel. */
export function NodeProfileSelect({
  nodeId,
  backendProfiles,
  onChange,
}: {
  nodeId: string;
  backendProfiles: BackendProfiles | null;
  onChange: (next: BackendProfiles) => void;
}) {
  const profiles = listProfiles(backendProfiles);
  const override = backendProfiles?.agent_assignments?.overrides?.[nodeId] ?? "";
  const resolved = resolveNodeProfile(backendProfiles, nodeId);

  if (profiles.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No backend profiles yet. Add them in the pipeline’s “LLM per node” panel (deselect the node)
        to override which LLM runs this node.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <ProfileSelect
        ariaLabel={`Backend profile for node ${nodeId}`}
        value={override}
        profiles={profiles}
        defaultLabel={getDefaultProfile(backendProfiles) ?? "none"}
        onChange={(profileId) => onChange(setNodeOverride(backendProfiles, nodeId, profileId))}
      />
      <p className="text-[10px] text-muted-foreground">
        Runs with: <span className="font-mono">{resolved ?? "the node’s own agent config"}</span>
      </p>
    </div>
  );
}

/** Pipeline-wide table: every node → profile, plus the default and a profile
 *  authoring form. Shown in the inspector when nothing is selected. */
export function BackendProfilePanel({
  nodes,
  backendProfiles,
  onChange,
}: {
  nodes: NodeRef[];
  backendProfiles: BackendProfiles | null;
  onChange: (next: BackendProfiles) => void;
}) {
  const profiles = listProfiles(backendProfiles);
  const available = backendProfiles?.available ?? {};
  const defaultProfile = getDefaultProfile(backendProfiles);

  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-1">LLM per node</h4>
        <p className="text-[11px] text-muted-foreground">
          Override which backend/model runs each node. The engine applies these at run time.
        </p>
      </div>

      <label className="block space-y-1">
        <span className="text-xs font-medium">Default profile</span>
        <select
          aria-label="Default backend profile"
          value={defaultProfile ?? ""}
          onChange={(e) => onChange(setDefaultProfile(backendProfiles, e.target.value || null))}
          className="w-full rounded-md border bg-background px-2 py-1 text-xs outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">(none — nodes use their own agent config)</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {profiles.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No profiles defined. Add one below, then assign it per node.
        </p>
      ) : (
        <div className="space-y-1.5">
          <span className="text-xs font-medium">Nodes</span>
          {nodes.length === 0 && (
            <p className="text-[11px] text-muted-foreground">This pipeline has no nodes yet.</p>
          )}
          {nodes.map((node) => (
            <div key={node.id} className="flex items-center gap-2">
              <span className="w-28 shrink-0 truncate text-xs font-mono" title={node.label}>
                {node.label}
              </span>
              <div className="flex-1">
                <ProfileSelect
                  ariaLabel={`Backend profile for ${node.label}`}
                  value={backendProfiles?.agent_assignments?.overrides?.[node.id] ?? ""}
                  profiles={profiles}
                  defaultLabel={defaultProfile ?? "none"}
                  onChange={(profileId) =>
                    onChange(setNodeOverride(backendProfiles, node.id, profileId))
                  }
                />
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-1.5 border-t pt-3">
        <span className="text-xs font-medium">Profiles</span>
        {profiles.map((p) => (
          <div key={p.id} className="flex items-center gap-2 text-xs">
            <Badge variant="secondary">{p.label}</Badge>
            <span className="flex-1 truncate font-mono text-[10px] text-muted-foreground">
              {profileSummary(available[p.id])}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              aria-label={`Remove profile ${p.label}`}
              onClick={() => onChange(removeProfile(backendProfiles, p.id))}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
        <AddProfileForm
          existingIds={profiles.map((p) => p.id)}
          onAdd={(id, profile) => onChange(addProfile(backendProfiles, id, profile))}
        />
      </div>
    </div>
  );
}

function AddProfileForm({
  existingIds,
  onAdd,
}: {
  existingIds: string[];
  onAdd: (id: string, profile: { runtime_id: string; runtime_config: Record<string, unknown> }) => void;
}) {
  const [id, setId] = useState("");
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("");

  const trimmedId = id.trim();
  const duplicate = existingIds.includes(trimmedId);
  const canAdd = trimmedId.length > 0 && !duplicate;

  return (
    <div className="space-y-1.5 rounded-md border bg-muted/30 p-2">
      <Input
        value={id}
        onChange={(e) => setId(e.target.value)}
        placeholder="Profile id (e.g. openai-gpt4o)"
        className="h-7 text-xs"
        aria-label="New profile id"
      />
      <div className="flex gap-1.5">
        <select
          aria-label="New profile provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className="h-7 rounded-md border bg-background px-1.5 text-xs outline-none"
        >
          {["anthropic", "openai", "deepseek", "gemini", "ollama"].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <Input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="model id"
          className="h-7 flex-1 text-xs"
          aria-label="New profile model id"
        />
      </div>
      {duplicate && <p className="text-[10px] text-destructive">A profile with that id exists.</p>}
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="w-full h-7 text-xs"
        disabled={!canAdd}
        onClick={() => {
          onAdd(trimmedId, {
            runtime_id: "api-call",
            runtime_config: {
              provider,
              ...(model.trim() ? { model_id: model.trim() } : {}),
            },
          });
          setId("");
          setModel("");
        }}
      >
        Add profile
      </Button>
    </div>
  );
}

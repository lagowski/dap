"use client";

import { useState } from "react";
import { AlertCircle, CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { BackendProfilesInspectionResponse } from "@/lib/api/types";
import {
  selectableDefaultProfile,
  withDefaultBackendProfile,
} from "@/lib/backend-profile-import";
import type { PipelineExport } from "@/lib/api/types";

interface BackendProfileImportDialogProps {
  open: boolean;
  bundle: PipelineExport | null;
  inspection: BackendProfilesInspectionResponse | null;
  pending: boolean;
  onCancel: () => void;
  onConfirm: (bundle: PipelineExport) => void;
}

export function BackendProfileImportDialog({
  open,
  bundle,
  inspection,
  pending,
  onCancel,
  onConfirm,
}: BackendProfileImportDialogProps) {
  const [selectedProfile, setSelectedProfile] = useState<string>(
    () => (inspection ? selectableDefaultProfile(inspection) : null) ?? "",
  );

  const profiles = inspection?.profiles ?? [];
  const selected = profiles.find((profile) => profile.id === selectedProfile);
  const canConfirm = !!bundle && !!selectedProfile && !pending;

  const handleConfirm = () => {
    if (!bundle || !selectedProfile) return;
    onConfirm(withDefaultBackendProfile(bundle, selectedProfile));
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onCancel()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Configure backends</DialogTitle>
          <DialogDescription>
            Select the default backend profile for this imported pipeline.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label
              htmlFor="default-backend-profile"
              id="default-backend-profile-label"
            >
              Default profile
            </Label>
            <select
              id="default-backend-profile"
              aria-labelledby="default-backend-profile-label"
              autoFocus
              value={selectedProfile}
              onChange={(event) => setSelectedProfile(event.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
              disabled={pending}
            >
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.label} {profile.available ? "" : "(unavailable)"}
                </option>
              ))}
            </select>
            {selected && !selected.available ? (
              <p className="flex items-center gap-1.5 text-xs text-amber-600">
                <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                This profile has unmet requirements. You can import now and
                configure the engine before running the pipeline.
              </p>
            ) : null}
          </div>

          <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className="rounded-md border border-input p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium">{profile.label}</div>
                    <div className="font-mono text-[11px] text-muted-foreground">
                      {profile.id}
                    </div>
                  </div>
                  <Badge
                    variant={profile.available ? "secondary" : "outline"}
                    className="shrink-0"
                  >
                    {profile.available ? (
                      <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      <XCircle className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    {profile.available ? "Available" : "Unavailable"}
                  </Badge>
                </div>

                {profile.description ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {profile.description}
                  </p>
                ) : null}

                <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  <RequirementBlock
                    title="Environment"
                    values={profile.requires_env ?? []}
                    missing={profile.missing_env ?? []}
                    emptyLabel="No env vars required"
                  />
                  <RequirementBlock
                    title="Service"
                    values={profile.requires_service ? [profile.requires_service] : []}
                    missing={
                      profile.requires_service && profile.service_available === false
                        ? [profile.requires_service]
                        : []
                    }
                    emptyLabel="No local service required"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button type="button" onClick={handleConfirm} disabled={!canConfirm}>
            {pending ? "Importing..." : "Import pipeline"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RequirementBlock({
  title,
  values,
  missing,
  emptyLabel,
}: {
  title: string;
  values: string[];
  missing: string[];
  emptyLabel: string;
}) {
  const missingSet = new Set(missing);
  return (
    <div className="rounded-md bg-muted/40 p-2">
      <div className="mb-1 font-medium">{title}</div>
      {values.length === 0 ? (
        <div className="text-muted-foreground">{emptyLabel}</div>
      ) : (
        <div className="flex flex-wrap gap-1">
          {values.map((value) => (
            <Badge
              key={value}
              variant={missingSet.has(value) ? "outline" : "secondary"}
              className="font-mono text-[10px]"
            >
              {value}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

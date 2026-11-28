import type {
  BackendProfilesInspectionResponse,
  PipelineExport,
} from "@/lib/api/types";

export function bundleHasBackendProfiles(bundle: PipelineExport): boolean {
  const profiles = bundle.backend_profiles;
  if (!profiles || typeof profiles !== "object") return false;
  const available = (profiles as { available?: unknown }).available;
  return !!available && typeof available === "object" && !Array.isArray(available);
}

export function hasInspectableProfiles(
  inspection: BackendProfilesInspectionResponse,
): boolean {
  return (inspection.profiles ?? []).length > 0;
}

export function selectableDefaultProfile(
  inspection: BackendProfilesInspectionResponse,
): string | null {
  const profiles = inspection.profiles ?? [];
  const defaultProfile = inspection.default_profile;
  if (defaultProfile && profiles.some((profile) => profile.id === defaultProfile)) {
    return defaultProfile;
  }
  return profiles.find((profile) => profile.available)?.id ?? profiles[0]?.id ?? null;
}

export function withDefaultBackendProfile(
  bundle: PipelineExport,
  defaultProfile: string,
): PipelineExport {
  const currentProfiles =
    bundle.backend_profiles && typeof bundle.backend_profiles === "object"
      ? bundle.backend_profiles
      : {};
  const currentAssignments =
    "agent_assignments" in currentProfiles &&
    currentProfiles.agent_assignments &&
    typeof currentProfiles.agent_assignments === "object"
      ? currentProfiles.agent_assignments
      : {};

  return {
    ...bundle,
    backend_profiles: {
      ...currentProfiles,
      agent_assignments: {
        ...currentAssignments,
        default_profile: defaultProfile,
      },
    },
  };
}

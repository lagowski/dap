import { describe, it, expect } from "vitest";
import type { ProjectFormValues } from "./project-form";
import type { ProjectCreate } from "@/lib/api/types";

describe("ProjectFormValues", () => {
  it("includes a pipelines field", () => {
    const values: ProjectFormValues = {
      name: "test",
      description: "desc",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: { cortex: "uuid-1" },
    };
    expect(values.pipelines).toEqual({ cortex: "uuid-1" });
  });

  it("round-trips cleanly with empty pipelines", () => {
    const values: ProjectFormValues = {
      name: "test",
      description: "",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: {},
    };
    expect(values.pipelines).toEqual({});
  });

  it("supports pre-populated edit state", () => {
    const values: ProjectFormValues = {
      name: "my-project",
      description: "desc",
      working_directory: "/var/work",
      repo_url: "https://github.com/org/repo.git",
      default_branch: "main",
      env_vars: {},
      pipelines: { deploy: "uuid-2", review: "uuid-3" },
    };
    expect(values.pipelines).toEqual({ deploy: "uuid-2", review: "uuid-3" });
  });

  it("is structurally compatible with ProjectCreate.pipelines", () => {
    const formValues: ProjectFormValues = {
      name: "test",
      description: "",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: { cortex: "uuid-1" },
    };
    // ProjectCreate.pipelines accepts Record<string, string> | undefined
    const payload: ProjectCreate = {
      name: formValues.name,
      pipelines: formValues.pipelines,
    };
    expect(payload.pipelines).toEqual({ cortex: "uuid-1" });
  });
});

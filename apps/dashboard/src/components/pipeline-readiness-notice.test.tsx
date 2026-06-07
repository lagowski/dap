import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineReadinessNotice } from "./pipeline-readiness-notice";
import type { PipelineReadiness } from "@/lib/api/types";

const ready: PipelineReadiness = {
  ready: true,
  checks: [
    { node_id: "n1", agent_id: "a1", callable_path: "json:dumps", resolvable: true, error: null },
  ],
};

const blocked: PipelineReadiness = {
  ready: false,
  checks: [
    { node_id: "n1", agent_id: "a1", callable_path: "json:dumps", resolvable: true, error: null },
    {
      node_id: "coder",
      agent_id: "a2",
      callable_path: "cortex.nodes.coder:run",
      resolvable: false,
      error: "python-func: cannot import 'cortex.nodes.coder:run' (ModuleNotFoundError: No module named 'cortex')",
    },
  ],
};

describe("PipelineReadinessNotice (#710)", () => {
  it("renders nothing while loading", () => {
    const { container } = render(<PipelineReadinessNotice readiness={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when every node resolves", () => {
    const { container } = render(<PipelineReadinessNotice readiness={ready} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the blocked node, its callable, and the error", () => {
    render(<PipelineReadinessNotice readiness={blocked} />);
    expect(screen.getByText(/1 node can.?t run on this engine/i)).toBeInTheDocument();
    expect(screen.getByText("coder")).toBeInTheDocument();
    expect(screen.getByText("cortex.nodes.coder:run")).toBeInTheDocument();
    expect(screen.getByText(/cannot import/i)).toBeInTheDocument();
    // The resolvable node is not listed as a problem.
    expect(screen.queryByText("n1")).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CallableDoc } from "./callable-doc";
import type { AgentCallableInfo } from "@/lib/api/types";

describe("CallableDoc (#747)", () => {
  it("shows a loading state while info is undefined", () => {
    render(<CallableDoc info={undefined} />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders the callable docstring", () => {
    const info: AgentCallableInfo = {
      callable_path: "cortex.nodes.finalize:run",
      resolvable: true,
      doc: "Finalize node — gate readiness and route to merge.\n\nReads dispatcher output.",
      error: null,
    };
    render(<CallableDoc info={info} />);
    expect(screen.getByText(/Finalize node — gate readiness/)).toBeInTheDocument();
    expect(screen.getByText(/Reads dispatcher output/)).toBeInTheDocument();
  });

  it("shows the import error when the callable can't resolve", () => {
    const info: AgentCallableInfo = {
      callable_path: "cortex.nodes.finalize:run",
      resolvable: false,
      doc: null,
      error: "python-func: cannot import 'cortex.nodes.finalize:run' (ModuleNotFoundError)",
    };
    render(<CallableDoc info={info} />);
    expect(screen.getByText(/can.?t be imported on this engine/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot import/)).toBeInTheDocument();
  });

  it("notes when a resolvable callable has no docstring", () => {
    const info: AgentCallableInfo = {
      callable_path: "examplepipe.gates:noop",
      resolvable: true,
      doc: null,
      error: null,
    };
    render(<CallableDoc info={info} />);
    expect(screen.getByText(/has no docstring/i)).toBeInTheDocument();
    expect(screen.getByText("examplepipe.gates:noop")).toBeInTheDocument();
  });
});

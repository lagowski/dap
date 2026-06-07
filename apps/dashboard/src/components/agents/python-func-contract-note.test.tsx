import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PythonFuncContractNote } from "./python-func-contract-note";

describe("PythonFuncContractNote (#739)", () => {
  it("explains inputs are documentary for python-func", () => {
    render(<PythonFuncContractNote kind="inputs" />);
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByText(/receive the full state directly/i)).toBeInTheDocument();
    expect(screen.getByText(/documentary only/i)).toBeInTheDocument();
  });

  it("explains outputs are documentary for python-func", () => {
    render(<PythonFuncContractNote kind="outputs" />);
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByText(/merged as-is/i)).toBeInTheDocument();
    expect(screen.getByText(/output_schema/)).toBeInTheDocument();
  });
});

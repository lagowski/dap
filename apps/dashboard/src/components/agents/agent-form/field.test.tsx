/**
 * Component test for the agent-form Field wrapper (audit D5).
 *
 * Field is tiny (Label + children + optional error <p>) but it's
 * used on every row of the agent form, so a regression here would
 * silently break ARIA + visual consistency across every form field
 * at once. The cost of these tests is ~30 LOC; the upside is a
 * sentinel that catches "someone deleted the error <p>" or
 * "someone forgot to render the children" in a future refactor.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Field } from "./field";


describe("Field", () => {
  it("renders the label text and the child input", () => {
    render(
      <Field label="Email">
        <input data-testid="child-input" />
      </Field>,
    );
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByTestId("child-input")).toBeInTheDocument();
  });

  it("renders nothing for the error region when error is undefined", () => {
    render(
      <Field label="Email">
        <input data-testid="child-input" />
      </Field>,
    );
    // The error <p> uses ``text-destructive`` styling; absence of any
    // element with that class proves no stray red text rendered when
    // there's no error to report.
    expect(document.querySelector(".text-destructive")).toBeNull();
  });

  it("renders the error message when error is set", () => {
    render(
      <Field label="Email" error="Invalid format">
        <input data-testid="child-input" />
      </Field>,
    );
    const error = screen.getByText("Invalid format");
    expect(error).toBeInTheDocument();
    expect(error).toHaveClass("text-destructive");
  });

  it("treats empty-string error as no error (truthy guard)", () => {
    // The component uses `error && <p>...` so empty string -> nothing.
    // This pins that branch so a future refactor to ``error != null``
    // doesn't accidentally start rendering an empty red strip.
    render(
      <Field label="Email" error="">
        <input data-testid="child-input" />
      </Field>,
    );
    expect(document.querySelector(".text-destructive")).toBeNull();
  });

  it("renders multiple children in document order", () => {
    render(
      <Field label="Compound">
        <input data-testid="first" placeholder="first" />
        <span data-testid="hint">hint</span>
      </Field>,
    );
    const first = screen.getByTestId("first");
    const hint = screen.getByTestId("hint");
    // ``compareDocumentPosition`` returns FOLLOWING (4) when ``hint``
    // appears after ``first`` in the tree. Locks in that React doesn't
    // silently reorder children.
    expect(first.compareDocumentPosition(hint)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});

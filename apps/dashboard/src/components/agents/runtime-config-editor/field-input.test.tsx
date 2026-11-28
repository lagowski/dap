/**
 * Component test for FieldInput (audit D5 — first component test
 * landing alongside the jsdom + RTL infra wiring).
 *
 * Doubles as the canary that the new test pipeline works: if this
 * goes red the whole component-test track is broken, not just one
 * component. Kept tight on purpose — covers the four inline kinds
 * (text / number / boolean / select), the per-field decorations
 * (asterisk for required, description text, error alert), and the
 * delegation to JsonFieldInput / KeyValueEditor for the json + kv
 * branches.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { RuntimeField } from "../runtime-config-schemas";

import { FieldInput } from "./field-input";


function makeField(overrides: Partial<RuntimeField> = {}): RuntimeField {
  return {
    key: "test_key",
    label: "Test field",
    kind: "text",
    ...overrides,
  };
}


/**
 * Controlled-input harness. FieldInput is a controlled component:
 * each keystroke fires onChange and the parent is expected to re-render
 * with the new value. Static-prop'd tests would snap the input back to
 * the initial value after every key, so we wire a tiny stateful wrapper
 * and spy on the change handler.
 */
function ControlledField({
  field,
  initial,
  onChange,
  error,
  onJsonError = () => undefined,
}: {
  field: RuntimeField;
  initial: unknown;
  onChange: (next: unknown) => void;
  error?: string;
  onJsonError?: (msg: string | null) => void;
}) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <FieldInput
      field={field}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
      error={error}
      onJsonError={onJsonError}
    />
  );
}


describe("FieldInput", () => {
  it("renders label + text input and emits the typed string on change", async () => {
    const onChange = vi.fn();
    render(
      <ControlledField
        field={makeField({ kind: "text", placeholder: "type here" })}
        initial=""
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Test field");
    await userEvent.type(input, "hi");
    // user.type fires one change per character; assert the latest captures
    // the final value the parent would store.
    expect(onChange).toHaveBeenLastCalledWith("hi");
  });

  it("renders an asterisk next to required field labels", () => {
    render(
      <FieldInput
        field={makeField({ required: true })}
        value=""
        onChange={() => undefined}
        onJsonError={() => undefined}
      />,
    );
    // The asterisk is a sibling of the label text; we look it up by class
    // since it has no role/label of its own — this is the cheapest way to
    // assert the required-marker hasn't silently disappeared.
    const asterisk = screen.getByText("*");
    expect(asterisk).toBeInTheDocument();
    expect(asterisk).toHaveClass("text-destructive");
  });

  it("renders description hint when provided", () => {
    render(
      <FieldInput
        field={makeField({ description: "Used to seed the run." })}
        value=""
        onChange={() => undefined}
        onJsonError={() => undefined}
      />,
    );
    expect(screen.getByText("Used to seed the run.")).toBeInTheDocument();
  });

  it("renders error message with role=alert when error prop set", () => {
    render(
      <FieldInput
        field={makeField()}
        value=""
        onChange={() => undefined}
        error="Required field"
        onJsonError={() => undefined}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Required field");
  });

  it("number input emits undefined when cleared and a number on input", async () => {
    const onChange = vi.fn();
    render(
      <ControlledField
        field={makeField({ kind: "number" })}
        initial={42}
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Test field");
    await userEvent.clear(input);
    // Clearing emits undefined so consumers can distinguish "unset" from
    // "the number 0". After clearing, typing a single digit must produce
    // a Number, not a string — the parent state shape demands it.
    expect(onChange).toHaveBeenLastCalledWith(undefined);

    onChange.mockClear();
    await userEvent.type(input, "7");
    expect(onChange).toHaveBeenLastCalledWith(7);
    expect(typeof onChange.mock.calls.at(-1)?.[0]).toBe("number");
  });

  it("boolean input toggles via checkbox", async () => {
    const onChange = vi.fn();
    render(
      <FieldInput
        field={makeField({ kind: "boolean" })}
        value={false}
        onChange={onChange}
        onJsonError={() => undefined}
      />,
    );
    const checkbox = screen.getByLabelText("Test field");
    await userEvent.click(checkbox);
    expect(onChange).toHaveBeenLastCalledWith(true);
  });

  it("select input renders all options and emits the chosen value", async () => {
    const onChange = vi.fn();
    render(
      <FieldInput
        field={makeField({
          kind: "select",
          options: [
            { value: "a", label: "Alpha" },
            { value: "b", label: "Beta" },
          ],
        })}
        value="a"
        onChange={onChange}
        onJsonError={() => undefined}
      />,
    );
    expect(screen.getByRole("option", { name: "Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Beta" })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Test field"), "b");
    expect(onChange).toHaveBeenLastCalledWith("b");
  });

  it("select input emits undefined when empty value is selected", async () => {
    const onChange = vi.fn();
    render(
      <FieldInput
        field={makeField({
          kind: "select",
          options: [
            { value: "", label: "— Pick —" },
            { value: "a", label: "Alpha" },
          ],
        })}
        value="a"
        onChange={onChange}
        onJsonError={() => undefined}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText("Test field"), "");
    // "" → undefined so downstream pruners drop the field rather than
    // sending an empty string the engine would reject.
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("json kind delegates to JsonFieldInput (textarea rendered)", () => {
    render(
      <FieldInput
        field={makeField({ kind: "json", default: {} })}
        value={{}}
        onChange={() => undefined}
        onJsonError={() => undefined}
      />,
    );
    // JsonFieldInput renders a <textarea> — the exact id is wired through
    // by FieldInput so we look it up via the label.
    const textarea = screen.getByLabelText("Test field");
    expect(textarea.tagName).toBe("TEXTAREA");
  });

  it("kv kind delegates to KeyValueEditor and shows the 'Add' control", () => {
    render(
      <FieldInput
        field={makeField({ kind: "kv" })}
        value={{ X_KEY: "v" }}
        onChange={() => undefined}
        onJsonError={() => undefined}
      />,
    );
    // The KV editor exposes an Add button — its presence proves the
    // delegate rendered (a missing/broken import would error or render
    // nothing).
    expect(screen.getByRole("button", { name: /add/i })).toBeInTheDocument();
  });
});

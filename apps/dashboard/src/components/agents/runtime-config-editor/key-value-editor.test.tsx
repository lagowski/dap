/**
 * Component test for KeyValueEditor (audit D5).
 *
 * KV editor is the trickiest piece in the runtime-config editor:
 * it keeps an internal ``rows`` array distinct from the parent's
 * ``Record<string, string>`` so a user typing a key transiently
 * (empty string → final name) doesn't drop rows on every keystroke
 * via Record collapse. Three regressions worth a test net:
 *
 * 1. Initial state mirrors ``value`` 1:1.
 * 2. Adding a blank row doesn't immediately emit an ``onChange``
 *    with that empty-keyed row included — empty keys are filtered
 *    by ``rowsToObject``, so the resulting object stays unchanged
 *    until the user types a real key.
 * 3. Duplicate keys produce an inline warning + style hook, and the
 *    last-write-wins behaviour matches what the user is being
 *    warned about (i.e. the warning isn't lying).
 *
 * Mutations go through ``ControlledKV`` because the component reads
 * back its parent's ``value`` prop to sync on external resets —
 * static-prop tests would mask broken handlers.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { KeyValueEditor } from "./key-value-editor";


/**
 * Stateful test harness — the editor is a controlled component that
 * also self-syncs to external resets. Without a real parent holding
 * state, ``onChange`` would never feed back into the next render.
 */
function ControlledKV({
  initial,
  onChange,
}: {
  initial: Record<string, string>;
  onChange?: (next: Record<string, string> | undefined) => void;
}) {
  const [value, setValue] = useState<Record<string, string>>(initial);
  return (
    <KeyValueEditor
      id="test-kv"
      value={value}
      onChange={(next) => {
        setValue(next ?? {});
        onChange?.(next);
      }}
    />
  );
}


describe("KeyValueEditor", () => {
  it("renders one row per initial entry", () => {
    render(
      <ControlledKV initial={{ FOO: "1", BAR: "2" }} />,
    );
    expect(screen.getByDisplayValue("FOO")).toBeInTheDocument();
    expect(screen.getByDisplayValue("BAR")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2")).toBeInTheDocument();
  });

  it("emits undefined when the last row is removed (empty dict → unset)", async () => {
    // Pinning a real production contract: when the editor empties out
    // it must emit ``undefined`` (not ``{}``). The parent orchestrator
    // (runtime-config-editor.tsx:89-96) keys on ``next === undefined``
    // to delete the field key from the runtime_config dict — if we
    // emitted ``{}`` instead, the key would survive as an empty dict
    // and the engine's payload would carry a no-op field.
    //
    // The spy receives the raw ``next`` value before the ControlledKV
    // harness coerces ``undefined → {}`` for its own re-render (which
    // is just so the editor's non-nullable ``value`` prop stays
    // valid). The assertion below checks the editor's emission, not
    // the harness's coerced state.
    const onChange = vi.fn();
    render(<ControlledKV initial={{ FOO: "1" }} onChange={onChange} />);

    // Find the delete button by scoping into the row that owns the
    // FOO input. ``getAllByRole("button")[0]`` would work today but
    // is order-dependent — an icon shuffle or layout change could
    // produce a false pass.
    const fooKeyInput = screen.getByDisplayValue("FOO");
    const fooRow = fooKeyInput.closest("div")!;
    const deleteButton = within(fooRow).getAllByRole("button").at(-1)!;
    await userEvent.click(deleteButton);

    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("'Add entry' creates a blank row that doesn't change the emitted dict", async () => {
    const onChange = vi.fn();
    render(
      <ControlledKV initial={{ FOO: "1" }} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));
    // The new row has empty key — ``rowsToObject`` skips empty keys —
    // so the parent's view of the dict should stay { FOO: "1" }.
    expect(onChange).toHaveBeenLastCalledWith({ FOO: "1" });
    // …but a new blank KEY input is in the DOM (the user can now type).
    const keyInputs = screen
      .getAllByPlaceholderText("KEY")
      .filter((el) => (el as HTMLInputElement).value === "");
    expect(keyInputs).toHaveLength(1);
  });

  it("typing into a new row updates the emitted dict once the key is non-empty", async () => {
    const onChange = vi.fn();
    render(
      <ControlledKV initial={{ FOO: "1" }} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));
    const blankKey = screen
      .getAllByPlaceholderText("KEY")
      .find((el) => (el as HTMLInputElement).value === "")!;
    await userEvent.type(blankKey, "BAR");

    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last).toEqual({ FOO: "1", BAR: "" });
  });

  it("flags duplicate keys with destructive style + inline warning", async () => {
    render(<ControlledKV initial={{ DUP: "first" }} />);
    // Add a second row + type the same key into it.
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));
    const blankKey = screen
      .getAllByPlaceholderText("KEY")
      .find((el) => (el as HTMLInputElement).value === "")!;
    await userEvent.type(blankKey, "DUP");

    // Both DUP rows must visually flag the conflict.
    const dupKeyInputs = screen.getAllByDisplayValue("DUP");
    expect(dupKeyInputs).toHaveLength(2);
    for (const input of dupKeyInputs) {
      expect(input).toHaveClass("border-destructive");
    }

    // Inline warning copy locks in the "last-write-wins" promise the
    // emitted dict actually makes (see test below).
    expect(
      screen.getAllByText(/duplicate key — only the last value survives/i),
    ).toHaveLength(2);
  });

  it("last-write-wins on duplicate keys matches the displayed warning", async () => {
    const onChange = vi.fn();
    render(
      <ControlledKV initial={{ DUP: "first" }} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));

    // Find the blank row's two inputs (key + value).
    const blankKey = screen
      .getAllByPlaceholderText("KEY")
      .find((el) => (el as HTMLInputElement).value === "")! as HTMLInputElement;
    const blankRow = blankKey.closest("div")!;
    const blankValue = within(blankRow).getByPlaceholderText("value");

    await userEvent.type(blankKey, "DUP");
    await userEvent.type(blankValue, "second");

    // The warning says "only the last value survives". The emitted
    // dict must agree: DUP → "second", not "first".
    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last).toEqual({ DUP: "second" });
  });

  it("ignores empty-key rows when emitting the dict", async () => {
    const onChange = vi.fn();
    render(
      <ControlledKV initial={{ FOO: "1" }} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));
    const blankRow = screen
      .getAllByPlaceholderText("KEY")
      .find((el) => (el as HTMLInputElement).value === "")!
      .closest("div")!;
    const blankValue = within(blankRow).getByPlaceholderText("value");
    // Type a value but never a key — row stays "in-progress" and
    // must not pollute the emitted dict.
    await userEvent.type(blankValue, "orphan");

    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last).toEqual({ FOO: "1" });
    // Empty-string key must not leak into the emitted dict. Using
    // ``Object.keys`` rather than ``toHaveProperty("")`` because the
    // latter parses its argument as a path and chokes on "".
    expect(Object.keys(last ?? {})).not.toContain("");
  });
});

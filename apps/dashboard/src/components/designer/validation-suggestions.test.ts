import { describe, expect, it } from "vitest";

import { distinctSuggestions, suggestionForWarning } from "./validation-suggestions";

describe("suggestionForWarning", () => {
  it("advises on an unused-output warning, naming the node and field", () => {
    const advice = suggestionForWarning(
      "Node 'verifier' writes 'tests_passed' but no downstream node reads it.",
    );
    expect(advice).not.toBeNull();
    expect(advice).toContain("verifier");
    expect(advice).toContain("tests_passed");
    expect(advice).toContain("output schema");
  });

  it("returns null for an unrecognised message", () => {
    expect(suggestionForWarning("Some other warning we have no advice for.")).toBeNull();
  });
});

describe("distinctSuggestions", () => {
  it("collapses warnings of the same class to one hint per distinct advice", () => {
    const warnings = [
      "Node 'verifier' writes 'tests_passed' but no downstream node reads it.",
      "Node 'verifier' writes 'last_test_output' but no downstream node reads it.",
      "Some unrelated warning.",
    ];
    const hints = distinctSuggestions(warnings);
    // Two distinct fields → two distinct hints; the unrelated one contributes none.
    expect(hints).toHaveLength(2);
    expect(hints[0]).toContain("tests_passed");
    expect(hints[1]).toContain("last_test_output");
  });

  it("returns an empty list when nothing is recognised", () => {
    expect(distinctSuggestions(["nope", "still nope"])).toEqual([]);
  });
});

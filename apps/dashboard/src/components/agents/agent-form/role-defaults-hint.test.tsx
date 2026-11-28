/**
 * Component test for RoleDefaultsHint (audit D5).
 *
 * Three branches that actually carry behaviour:
 *
 * 1. Empty / missing ``recommended`` → render nothing (we don't want
 *    an empty "Typical X for ___:" strip on roles without
 *    conventions like ``post_check``).
 * 2. Current selection matches the recommendation → disable the
 *    "Use role defaults" button so a no-op click can't trigger a
 *    state-shape change.
 * 3. Selection differs → click hands back a fresh array *copy* (not
 *    the prop reference) so the parent can't accidentally mutate
 *    ROLE_DEFAULT_*_SCHEMA constants by mutating its own state.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RoleDefaultsHint } from "./role-defaults-hint";


describe("RoleDefaultsHint", () => {
  it("renders nothing when no recommendation for the role", () => {
    const { container } = render(
      <RoleDefaultsHint
        role="custom_role"
        recommended={undefined}
        current={[]}
        onUseDefaults={() => undefined}
        subjectLabel="inputs"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the recommendation is an empty list", () => {
    const { container } = render(
      <RoleDefaultsHint
        role="post_check"
        recommended={[]}
        current={["task_brief"]}
        onUseDefaults={() => undefined}
        subjectLabel="outputs"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables the button when current already matches recommendation", () => {
    render(
      <RoleDefaultsHint
        role="test_author"
        recommended={["task_brief", "repo_context"]}
        current={["task_brief", "repo_context"]}
        onUseDefaults={() => undefined}
        subjectLabel="inputs"
      />,
    );
    expect(
      screen.getByRole("button", { name: /use role defaults/i }),
    ).toBeDisabled();
  });

  it("treats order-insensitive matches as 'already applied'", () => {
    // ``matches`` uses ``every(... includes ...)`` so a user-reordered
    // list containing the same fields still counts as matching and
    // shouldn't show an actionable button.
    render(
      <RoleDefaultsHint
        role="test_author"
        recommended={["task_brief", "repo_context"]}
        current={["repo_context", "task_brief"]}
        onUseDefaults={() => undefined}
        subjectLabel="inputs"
      />,
    );
    expect(
      screen.getByRole("button", { name: /use role defaults/i }),
    ).toBeDisabled();
  });

  it("treats a same-length list with different content as non-matching", () => {
    // Same length, different members → must be actionable. Guards
    // against an accidental ``length === length``-only check.
    render(
      <RoleDefaultsHint
        role="test_author"
        recommended={["task_brief", "repo_context"]}
        current={["task_brief", "other_field"]}
        onUseDefaults={() => undefined}
        subjectLabel="inputs"
      />,
    );
    expect(
      screen.getByRole("button", { name: /use role defaults/i }),
    ).toBeEnabled();
  });

  it("calls back with a copy of the recommended list on click", async () => {
    const recommended = ["task_brief", "repo_context"] as const;
    const onUseDefaults = vi.fn();
    render(
      <RoleDefaultsHint
        role="test_author"
        recommended={recommended}
        current={["task_brief"]}
        onUseDefaults={onUseDefaults}
        subjectLabel="inputs"
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /use role defaults/i }),
    );

    expect(onUseDefaults).toHaveBeenCalledTimes(1);
    const handed = onUseDefaults.mock.calls[0][0];
    expect(handed).toEqual(["task_brief", "repo_context"]);
    // Mutation safety: parent must not be able to mutate the
    // ROLE_DEFAULT_* constants by mutating its own state.
    expect(handed).not.toBe(recommended);
  });

  it("renders the recommendation list and the role name", () => {
    render(
      <RoleDefaultsHint
        role="implementer"
        recommended={["task_brief", "repo_context", "tests"]}
        current={[]}
        onUseDefaults={() => undefined}
        subjectLabel="outputs"
      />,
    );
    expect(screen.getByText(/typical outputs for/i)).toBeInTheDocument();
    expect(screen.getByText("implementer")).toBeInTheDocument();
    expect(
      screen.getByText("task_brief, repo_context, tests"),
    ).toBeInTheDocument();
  });
});

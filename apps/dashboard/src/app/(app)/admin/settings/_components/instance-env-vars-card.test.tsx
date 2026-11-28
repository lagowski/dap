import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockList = vi.fn();
const mockUpsert = vi.fn();
const mockRemove = vi.fn();

vi.mock("@/hooks/api", () => ({
  useInstanceEnvVars: () => mockList(),
  useUpsertInstanceEnvVar: () => ({ mutate: mockUpsert, isPending: false, isError: false }),
  useDeleteInstanceEnvVar: () => ({ mutate: mockRemove, isPending: false, isError: false }),
}));

import { InstanceEnvVarsCard } from "./instance-env-vars-card";

describe("InstanceEnvVarsCard (#388 UI)", () => {
  beforeEach(() => {
    mockList.mockReset();
    mockUpsert.mockReset();
    mockRemove.mockReset();
  });

  it("lists existing env vars with masked previews", () => {
    mockList.mockReturnValue({
      data: { env_vars: [{ key: "MY_CUSTOM_KEY", preview: "clau••••", value_set: true }] },
      isPending: false,
      isError: false,
    });
    render(<InstanceEnvVarsCard />);
    expect(screen.getByText("MY_CUSTOM_KEY")).toBeInTheDocument();
    expect(screen.getByText("clau••••")).toBeInTheDocument();
  });

  it("shows an empty state when there are no vars", () => {
    mockList.mockReturnValue({ data: { env_vars: [] }, isPending: false, isError: false });
    render(<InstanceEnvVarsCard />);
    expect(screen.getByText(/no instance env vars set/i)).toBeInTheDocument();
  });

  it("adds a var via the form", async () => {
    mockList.mockReturnValue({ data: { env_vars: [] }, isPending: false, isError: false });
    const user = userEvent.setup();
    render(<InstanceEnvVarsCard />);

    await user.type(screen.getByLabelText(/env var name/i), "ASSISTANT_PROVIDER");
    await user.type(screen.getByLabelText(/env var value/i), "claude-code");
    await user.click(screen.getByRole("button", { name: /add \/ update/i }));

    expect(mockUpsert).toHaveBeenCalledTimes(1);
    expect(mockUpsert.mock.calls[0][0]).toEqual({
      key: "ASSISTANT_PROVIDER",
      value: "claude-code",
    });
  });

  it("deletes a var", async () => {
    mockList.mockReturnValue({
      data: { env_vars: [{ key: "OPENAI_API_KEY", preview: "sk-1••••", value_set: true }] },
      isPending: false,
      isError: false,
    });
    const user = userEvent.setup();
    render(<InstanceEnvVarsCard />);
    await user.click(screen.getByRole("button", { name: /delete openai_api_key/i }));
    expect(mockRemove).toHaveBeenCalledWith("OPENAI_API_KEY");
  });
});

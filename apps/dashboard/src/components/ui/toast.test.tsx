import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider, useToast } from "./toast";

function TestTrigger({ variant }: { variant?: "default" | "warning" | "destructive" }) {
  const toast = useToast();
  return (
    <button
      onClick={() =>
        toast({
          title: "Test toast",
          description: "A description",
          variant: variant ?? "default",
        })
      }
    >
      Fire
    </button>
  );
}

describe("Toast", () => {
  it("renders a toast when fired", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <TestTrigger />
      </ToastProvider>,
    );

    await user.click(screen.getByText("Fire"));
    expect(screen.getByText("Test toast")).toBeInTheDocument();
    expect(screen.getByText("A description")).toBeInTheDocument();
  });

  it("renders warning variant with amber styling", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <TestTrigger variant="warning" />
      </ToastProvider>,
    );

    await user.click(screen.getByText("Fire"));
    const toastEl = screen.getByText("Test toast").closest("[data-state]");
    expect(toastEl?.className).toContain("amber");
  });

  it("throws when useToast is called outside provider", () => {
    function Bad() {
      useToast();
      return null;
    }
    expect(() => render(<Bad />)).toThrow(
      "useToast must be used within <ToastProvider>",
    );
  });
});

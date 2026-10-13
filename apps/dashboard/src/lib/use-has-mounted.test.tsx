import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { useHasMounted } from "./use-has-mounted";

function Probe() {
  return <span data-testid="probe">{useHasMounted() ? "mounted" : "server"}</span>;
}

describe("useHasMounted (#917)", () => {
  it("is false when rendered on the server", () => {
    expect(renderToString(<Probe />)).toContain("server");
  });

  it("is true once mounted on the client", () => {
    render(<Probe />);
    expect(screen.getByTestId("probe").textContent).toBe("mounted");
  });
});

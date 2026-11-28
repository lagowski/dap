import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DbStatusPill } from "./db-status-pill";

// Mock the useHealth hook so we can drive the four label states (#622).
vi.mock("@/hooks/api", () => ({
  useHealth: vi.fn(),
}));

import { useHealth } from "@/hooks/api";

// Helper to type the mocked hook return without dragging in the full
// @tanstack/react-query Result type — only the fields the pill reads.
type HealthSnapshot = {
  isPending: boolean;
  isError: boolean;
  data?: { db_reachable: boolean; db_dialect: "postgresql" | "sqlite" };
};

const setHealth = (snapshot: HealthSnapshot) => {
  vi.mocked(useHealth).mockReturnValue(snapshot as ReturnType<typeof useHealth>);
};

describe("DbStatusPill (#622 — split engine-busy from db-down)", () => {
  it("shows 'Checking database…' while the health probe is pending", () => {
    setHealth({ isPending: true, isError: false });
    render(<DbStatusPill />);
    expect(screen.getByText("Checking database…")).toBeInTheDocument();
  });

  it("shows 'Engine busy or unavailable' (amber) on health-query error — DB status is unknown, do NOT misreport as red 'Database unreachable'", () => {
    setHealth({ isPending: false, isError: true });
    render(<DbStatusPill />);
    expect(
      screen.getByText("Engine busy or unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Database unreachable")).not.toBeInTheDocument();
  });

  it("shows 'Database unreachable' (red) only when engine responded successfully AND reports db_reachable=false", () => {
    setHealth({
      isPending: false,
      isError: false,
      data: { db_reachable: false, db_dialect: "postgresql" },
    });
    render(<DbStatusPill />);
    expect(screen.getByText("Database unreachable")).toBeInTheDocument();
  });

  it("shows 'Connected to PostgreSQL' (green) when engine responded and DB is reachable on postgres", () => {
    setHealth({
      isPending: false,
      isError: false,
      data: { db_reachable: true, db_dialect: "postgresql" },
    });
    render(<DbStatusPill />);
    expect(screen.getByText("Connected to PostgreSQL")).toBeInTheDocument();
  });

  it("shows 'Using local SQLite' (amber) for the local-dev default dialect", () => {
    setHealth({
      isPending: false,
      isError: false,
      data: { db_reachable: true, db_dialect: "sqlite" },
    });
    render(<DbStatusPill />);
    expect(screen.getByText("Using local SQLite")).toBeInTheDocument();
  });
});

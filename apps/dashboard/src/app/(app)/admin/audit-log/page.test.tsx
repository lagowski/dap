/**
 * Audit-log table semantics.
 *
 * Each event row is clickable (opens the detail dialog), but it must stay a
 * table *row*: overriding ``<tr>`` with ``role="button"`` leaves its cells
 * without a row parent, so assistive tech (and Playwright's
 * ``getByRole('row')``) no longer sees a table — the nightly e2e
 * ``admin/audit-log.spec.ts`` failed on exactly that (#870).
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AuditEvent } from "@/lib/api/types";

const EVENT: AuditEvent = {
  id: "evt-1",
  created_at: "2026-10-02T09:47:21Z",
  event_type: "user.registered",
  user_id: "b6598572-0000-4000-8000-000000000000",
  event_data: { email: "e2e-fixture@example.com" },
} as AuditEvent;

vi.mock("@/hooks/api", () => ({
  useAuditEvents: () => ({
    data: { items: [EVENT], total: 1 },
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

import AdminAuditLogPage from "./page";

describe("AdminAuditLogPage event rows", () => {
  it("renders each event as a table row containing its cells", () => {
    render(<AdminAuditLogPage />);

    const table = screen.getByRole("table");
    // Header row + one event row.
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByRole("cell", { name: "user.registered" })).toBeInTheDocument();
    expect(within(table).queryByRole("button")).not.toBeInTheDocument();
  });

  it("opens the detail dialog from the keyboard on the focused row", async () => {
    const user = userEvent.setup();
    render(<AdminAuditLogPage />);

    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    row.focus();
    expect(row).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

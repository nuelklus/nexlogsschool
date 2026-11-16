import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import TermClosurePanel, { type ClosureOverview } from "./term-closure-panel";

const overview = (status: string, can: Partial<ClosureOverview["permissions"]> = {}): ClosureOverview => ({
  term_name: "Term 1", academic_year_name: "2026/2027", start_date: "2026-01-01", end_date: "2026-04-30", today: "2026-05-02",
  status, is_completed: false, progress: { done: 1, required: 2 }, closure: null,
  permissions: { initialize: true, academic_approval: false, final_authorization: false, reopen: false, ...can },
  items: [
    { id: "1", category: "examinations", code: "marks_entered", title: "Subject marks entered", description: "", required: true, status: "incomplete", outstanding_count: 3, evidence_summary: "3 scores are missing.", details: { classes: ["Grade 1"] } },
    { id: "2", category: "reporting", code: "report_cards", title: "Report cards generated", description: "", required: false, status: "not_applicable", outstanding_count: 0, evidence_summary: "Not set up.", details: {} },
  ],
  exceptions: [],
  finance: { outstanding_invoice_count: 2, outstanding_total: "400.00", students_with_balances: 2, draft_invoice_count: 0, draft_invoice_total: "0.00", invoiced_total: "900.00", collected_total: "500.00", collections_by_method: [] },
  history: [],
});

function setup(data: ClosureOverview) {
  const authFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => data });
  render(<TermClosurePanel authFetch={authFetch} tenantId="t1" termId="x" onBack={() => undefined} onChanged={async () => undefined} onOpenTab={() => undefined} />);
  return authFetch;
}

describe("TermClosurePanel", () => {
  it("shows the checklist, not-applicable items and the financial summary", async () => {
    setup(overview("in_progress"));
    expect(await screen.findByText(/Subject marks entered/)).toBeInTheDocument();
    expect(screen.getByText(/Report cards generated.*Not applicable/)).toBeInTheDocument();
    expect(screen.getByText(/3 scores are missing/)).toBeInTheDocument();
    expect(screen.getByText("GHS 400.00")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Authorize final closure/ })).not.toBeInTheDocument();
  });

  it("lets only the headteacher approve a submitted closure", async () => {
    const authFetch = setup(overview("submitted_for_review", { academic_approval: true }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve academic completion" }));
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(2));
    expect(authFetch.mock.calls[1][0]).toContain("academic-approval");
  });

  it("offers a start button when no closure exists", async () => {
    setup(overview("not_started"));
    expect(await screen.findByRole("button", { name: "Start term closure" })).toBeInTheDocument();
  });

  it("renders when school-wide finance information is restricted", async () => {
    setup({ ...overview("in_progress"), finance: null });
    expect(await screen.findByText(/Subject marks entered/)).toBeInTheDocument();
    expect(screen.queryByText("Financial summary")).not.toBeInTheDocument();
  });
});

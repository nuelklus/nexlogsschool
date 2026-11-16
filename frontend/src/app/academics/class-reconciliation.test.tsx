import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassReconciliation from "./class-reconciliation";

describe("ClassReconciliation", () => {
  it("displays multiple candidates and student mismatches without mutating", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({
      current_year: { id: "y", name: "2026" },
      unmapped_classes: [{ id: "c", name: "Grade 1", section: "A", academic_year: "2026", candidates: [{ id: "l", name: "Basic 1" }, { id: "m", name: "Grade 1" }] }],
      student_issues: [{ id: "s", name: "Student", admission_number: "001", grade_level: "Grade 2", issue: "grade_mismatch", classes: [{ id: "c", name: "Grade 1", section: "A" }] }],
    }), { status: 200 }));
    render(<ClassReconciliation authFetch={fetch} tenantId="t" />);
    await userEvent.click(screen.getByRole("button", { name: "Load reconciliation" }));
    expect(await screen.findByText("Possible levels: Basic 1, Grade 1")).toBeInTheDocument();
    expect(screen.getByText(/Student grade differs from the enrolled class/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/t/academics/class-reconciliation/");
  });

  it("shows missing current year instead of claiming no issues", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({
      current_year: null, unmapped_classes: [], student_issues: [],
    }), { status: 200 }));
    render(<ClassReconciliation authFetch={fetch} tenantId="t" />);
    await userEvent.click(screen.getByRole("button", { name: "Load reconciliation" }));
    expect(await screen.findByText(/student checks have not run/)).toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "Not available" }), { status: 500 }));
    render(<ClassReconciliation authFetch={fetch} tenantId="t" />);
    await userEvent.click(screen.getByRole("button", { name: "Load reconciliation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not available");
  });
});

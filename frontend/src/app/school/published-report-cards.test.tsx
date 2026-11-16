import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import PublishedReportCards from "./published-report-cards";

it("loads available report-card versions from the authenticated endpoint", async () => {
  const fetch = vi.fn(async () => Response.json([{ id: "one", enrollment: "enrollment", version: 1, status: "published",
    snapshot: { student: "Ama", admission_number: "A1", term: "Term 1", year: "2026", class: "Basic 1",
      section: "A", campus: null, overall_average: "78.00", overall_method: "Equal weight",
      class_teacher_remark: "Good", headteacher_remark: "Well done", attendance: { present: 2 },
      results: [{ publication_id: "result", version: 1, result: { subject: "Maths", examination: "Term exam",
        score: "78.00", grade: "P", description: "Pass" } }] } }]));
  render(<PublishedReportCards tenantId="school" authFetch={fetch} />);
  await userEvent.setup().click(screen.getByRole("button", { name: "Load report cards" }));
  expect(await screen.findByText(/Ama · 2026 · Term 1 · version 1/)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/report-cards/", undefined);
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PublishedResults from "./published-results";

describe("Published results", () => {
  it("renders the authenticated portal response", async () => {
    const fetch = vi.fn(async () => Response.json([{
      id: "result", result: { student_name: "Ama Mensah", admission_number: "A1", year: "2026",
        term: "Term 1", class: "Basic 1", examination: "End of term", subject: "Maths",
        score: "78.00", grade: "P", description: "Pass",
        marks: [{ component: "exam", status: "scored", score: "80.00", remarks: "Good work" }] },
    }]));
    render(<PublishedResults tenantId="school" authFetch={fetch} />);
    expect(await screen.findByText("Ama Mensah · Maths: 78.00% (P)")).toBeInTheDocument();
    expect(screen.getByText("exam: 80.00 · Good work")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/published-results/");
  });
  it("shows a failure instead of pretending there are no results", async () => {
    const fetch = vi.fn(async () => Response.json({ detail: "Unable to load." }, { status: 500 }));
    render(<PublishedResults tenantId="school" authFetch={fetch} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load.");
    expect(screen.queryByText("No results have been published yet.")).not.toBeInTheDocument();
  });
});

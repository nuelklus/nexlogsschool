import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import ReportCardPreview from "./report-card-preview";
import type { ExamDetail } from "./examination-api";

it("previews only explicitly selected published examinations", async () => {
  const exam = { id: "exam", name: "Term exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [{ enrollment: "enrollment", student: "student", name: "Ama", admission_number: "A1" }],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ term: "term", enrollment: "enrollment", examinations: ["exam"], format: "json" });
    return Response.json({ student: "Ama", admission_number: "A1", attendance: { present: 2, absent: 1 },
      results: [{ publication_id: "publication", version: 1, result: { subject: "Maths", score: "78.00", grade: "P", description: "Pass" } }] });
  });
  render(<ReportCardPreview authFetch={fetch} tenantId="school" detail={detail} exams={[exam]}
    subjects={[{ id: "subject", name: "Maths", code: "MATH" }]} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Preview selected published results"));
  await user.selectOptions(screen.getByLabelText("Student"), "enrollment");
  await user.click(screen.getByRole("checkbox", { name: /Term exam · Maths/ }));
  await user.click(screen.getByRole("button", { name: "Show preview" }));
  expect(await screen.findByText(/Maths: 78.00%/)).toBeInTheDocument();
  expect(screen.getByText(/absent: 1/)).toBeInTheDocument();
  expect(screen.getByText(/not an official report card/i)).toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import ReportCardBatch from "./report-card-batch";
import type { ExamDetail } from "./examination-api";

it("loads a dated roster and reports per-student validation errors without claiming success", async () => {
  const exam = { id: "exam", name: "Term exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("class-roster/")) return Response.json({ year: "2026", term: "Term 1", class_name: "Basic 1",
      campus: null, students: [{ enrollment: "one", name: "Ama", admission_number: "A1" }] });
    expect(JSON.parse(String(init?.body))).toMatchObject({ entries: [{ enrollment: "one", examinations: ["exam"],
      class_teacher_remark: "Good", headteacher_remark: "Keep going" }] });
    return Response.json({ detail: "No reports were saved.", students: [
      { admission_number: "A1", errors: { examinations: ["Missing", "Another issue"], detail: "A reviewed version already awaits publication." } },
      { admission_number: "A2", errors: { nested: { reason: ["Required"] } } },
      { admission_number: "A3", errors: { examinations: null } },
      {},
    ] }, { status: 400 });
  });
  render(<ReportCardBatch authFetch={fetch} tenantId="school" detail={detail} exams={[exam]}
    subjects={[{ id: "subject", name: "Maths", code: "MATH" }]} onReviewed={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Review class report cards"));
  await user.click(screen.getByRole("button", { name: "Load review roster" }));
  expect(await screen.findByText(/2026 · Term 1 · Basic 1/)).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /Term exam · Maths/ }));
  await user.type(screen.getByLabelText("Class teacher remark"), "Good");
  await user.type(screen.getByLabelText("Headteacher remark"), "Keep going");
  await user.click(screen.getByRole("button", { name: "Review all reports" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No reports were saved.");
  const errors = screen.getByRole("list", { name: "Student review errors" });
  expect(errors).toHaveTextContent("A1: examinations: Missing; examinations: Another issue; A reviewed version already awaits publication.");
  expect(errors).toHaveTextContent("A2: nested · reason: Required");
  expect(errors).toHaveTextContent("A3: examinations: Unrecognized error from the server.");
  expect(errors).toHaveTextContent("Unknown student 4: Unrecognized error from the server.");
  expect(errors).not.toHaveTextContent("{");
});

it("reports successful review without publishing and warns if refresh fails", async () => {
  const exam = { id: "exam", name: "Exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const fetch = vi.fn(async (path: string) => path.endsWith("class-roster/")
    ? Response.json({ year: "2026", term: "Term 1", class_name: "Basic 1", campus: null,
      students: [{ enrollment: "one", name: "Ama", admission_number: "A1" }] })
    : Response.json({ count: 1, reports: [{ id: "review" }] }));
  const refresh = vi.fn().mockRejectedValue(new Error("network error"));
  render(<ReportCardBatch authFetch={fetch} tenantId="school" detail={detail} exams={[exam]}
    subjects={[{ id: "subject", name: "Maths", code: "MATH" }]} onReviewed={refresh} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Review class report cards"));
  await user.click(screen.getByRole("button", { name: "Load review roster" }));
  await user.click(await screen.findByRole("checkbox", { name: /Exam · Maths/ }));
  await user.type(screen.getByLabelText("Class teacher remark"), "Good");
  await user.type(screen.getByLabelText("Headteacher remark"), "Well done");
  await user.click(screen.getByRole("button", { name: "Review all reports" }));
  expect(await screen.findByRole("status")).toHaveTextContent("1 reports reviewed");
  expect(screen.getByRole("alert")).toHaveTextContent("Reports were reviewed, but the report list could not be refreshed");
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledOnce();
});

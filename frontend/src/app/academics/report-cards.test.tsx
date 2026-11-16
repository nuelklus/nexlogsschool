import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import ReportCards from "./report-cards";
import type { ExamDetail } from "./examination-api";

it("reviews a report with explicit subject selection and remarks", async () => {
  const exam = { id: "exam", name: "Term exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [{ enrollment: "enrollment", student: "student", name: "Ama", admission_number: "A1" }],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const report = { id: "report", enrollment: "enrollment", version: 1, status: "reviewed",
    snapshot: { student: "Ama", admission_number: "A1", term: "Term 1", year: "2026", class: "Basic 1",
      section: "A", campus: null, overall_average: "78.00", overall_method: "Equal weight",
      class_teacher_remark: "Good", headteacher_remark: "Well done", attendance: { present: 2 },
      results: [{ publication_id: "result", version: 1, result: { subject: "Maths", examination: "Term exam",
        score: "78.00", grade: "P", description: "Pass" } }] } };
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      expect(JSON.parse(String(init.body))).toMatchObject({ enrollment: "enrollment", examinations: ["exam"],
        class_teacher_remark: "Good", headteacher_remark: "Well done" });
      return Response.json(report);
    }
    return Response.json([report]);
  });

  render(<ReportCards authFetch={fetch} tenantId="school" role="headteacher" detail={detail} exams={[exam]}
    subjects={[{ id: "subject", name: "Maths", code: "MATH" }]} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Review a report card"));
  await user.selectOptions(screen.getByLabelText("Report student"), "enrollment");
  await user.click(screen.getByRole("checkbox", { name: /Term exam · Maths/ }));
  await user.type(screen.getByLabelText("Class teacher remark"), "Good");
  await user.type(screen.getByLabelText("Headteacher remark"), "Well done");
  await user.click(screen.getByRole("button", { name: "Review report" }));
  expect(await screen.findByText("Report reviewed and awaiting publication.")).toBeInTheDocument();
  expect(screen.getByText(/Ama · 2026 · Term 1 · v1 · reviewed/)).toBeInTheDocument();
  await user.click(screen.getByText("Inspect Ama v1"));
  expect(screen.getByText(/Overall: 78.00%/)).toBeInTheDocument();
  expect(screen.getByText("Headteacher remark: Well done")).toBeInTheDocument();
});

it("requests a class-wide export for the selected roster date", async () => {
  const exam = { id: "exam", name: "Term exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ term: "term", school_class: "class", roster_date: "2026-03-10" });
    return new Response("zip", { headers: { "Content-Type": "application/zip" } });
  });
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:test"), revokeObjectURL: vi.fn() }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<ReportCards authFetch={fetch} tenantId="school" role="owner" detail={detail} exams={[exam]} subjects={[]} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Download class report cards"));
  await user.click(screen.getByRole("button", { name: "Download class reports ZIP" }));
  await vi.waitFor(() => expect(click).toHaveBeenCalled());
  expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/report-cards/class-export/", expect.any(Object));
  click.mockRestore();
  vi.unstubAllGlobals();
});

it("shows missing-report errors rather than claiming an export succeeded", async () => {
  const exam = { id: "exam", name: "Exam", term: "term", school_class: "class", subject: "subject",
    configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11", marks_deadline: "2026-03-15",
    publication_date: "2026-03-16", status: "published", version: 1, published_version: 1 };
  const detail: ExamDetail = { ...exam, students: [],
    policy: { components: [], bands: [], decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false },
    marks: [], calculations: [], publication_history: [] };
  const fetch = vi.fn(async () => Response.json({ detail: "Published reports are missing for: A1" }, { status: 400 }));
  render(<ReportCards authFetch={fetch} tenantId="school" role="owner" detail={detail} exams={[exam]} subjects={[]} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Download class report cards"));
  await user.click(screen.getByRole("button", { name: "Download class reports ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Published reports are missing for: A1");
});

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ExaminationSessions from "./examination-sessions";

const exam = {
  id: "exam", name: "Term exams", term: "term", school_class: "class", subject: "subject",
  configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11",
  marks_deadline: "2026-03-15", publication_date: "2026-03-16", status: "draft", version: 1, published_version: 0,
};
const props = {
  tenantId: "school", canManage: true, canOpen: true,
  terms: [{ id: "term", academic_year: "year", name: "Term 1", start_date: "2026-01-01", end_date: "2026-04-30", is_current: true, is_completed: false }],
  classes: [{ id: "class", academic_year: "year", name: "Basic 1", section: "A", room: "" }],
  subjects: [{ id: "subject", name: "Maths", code: "MATH" }, { id: "english", name: "English", code: "ENG" }],
  configs: [{
    id: "config", family: "family", version: 1, name: "Policy", class_level: null, examination_type: "term",
    configuration: { components: [], bands: [], decimal_places: 2, rounding: "half_up" as const, allow_decimal_scores: true, exclude_not_applicable: false },
  }],
  onCreated: vi.fn(async () => {}), onOpen: vi.fn(async () => {}),
};

describe("Examination sessions", () => {
  it("creates independently scheduled entries and shows scoped progress", async () => {
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        expect(body.entries).toHaveLength(2);
        expect(body.entries[1]).toMatchObject({ subject: "english", start_date: "2026-03-12" });
        return Response.json({ id: "session", entries: [exam] }, { status: 201 });
      }
      return Response.json([{ id: "session", name: "Term exams", term: "term",
        entries: [{ ...exam, class_name: "Basic 1", section: "A", subject_name: "Maths" }],
        counts: { draft: 1, submitted: 0, approved: 0, published: 0 } }]);
    });
    render(<ExaminationSessions {...props} authFetch={fetch} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Create examination session"));
    await user.type(screen.getByLabelText("Session name"), "Term exams");
    await user.selectOptions(screen.getByLabelText("Session term"), "term");
    await user.click(screen.getByRole("button", { name: "Add class and subject" }));
    for (let index = 1; index <= 2; index++) {
      const row = within(screen.getByRole("group", { name: `Class and subject ${index}` }));
      await user.selectOptions(row.getByLabelText("Class"), "class");
      await user.selectOptions(row.getByLabelText("Subject"), index === 1 ? "subject" : "english");
      await user.selectOptions(row.getByLabelText("Grading configuration"), "config");
      await user.type(row.getByLabelText("Start date"), index === 1 ? "2026-03-10" : "2026-03-12");
      await user.type(row.getByLabelText("End date"), "2026-03-13");
      await user.type(row.getByLabelText("Marks deadline"), "2026-03-15");
      await user.type(row.getByLabelText("Publication date"), "2026-03-16");
    }
    await user.click(screen.getByRole("button", { name: "Create session" }));
    expect(await screen.findByText("Examination session created.")).toBeInTheDocument();
    expect(screen.getByText(/Visible entries: 1 · Draft: 1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open Basic 1 Maths" }));
    expect(props.onOpen).toHaveBeenCalledWith("exam");
  });

  it("warns when a created session cannot be refreshed without encouraging a duplicate", async () => {
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST"
      ? Response.json({ id: "session", entries: [exam] }, { status: 201 })
      : Response.json([]));
    render(<ExaminationSessions {...props} authFetch={fetch} onCreated={vi.fn().mockRejectedValue(new Error("offline"))} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Create examination session"));
    await user.type(screen.getByLabelText("Session name"), "Term exams");
    await user.selectOptions(screen.getByLabelText("Session term"), "term");
    const row = within(screen.getByRole("group", { name: "Class and subject 1" }));
    await user.selectOptions(row.getByLabelText("Class"), "class");
    await user.selectOptions(row.getByLabelText("Subject"), "subject");
    await user.selectOptions(row.getByLabelText("Grading configuration"), "config");
    await user.type(row.getByLabelText("Start date"), "2026-03-10");
    await user.type(row.getByLabelText("End date"), "2026-03-11");
    await user.type(row.getByLabelText("Marks deadline"), "2026-03-15");
    await user.type(row.getByLabelText("Publication date"), "2026-03-16");
    await user.click(screen.getByRole("button", { name: "Create session" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The session was created, but the lists could not be refreshed.");
    expect(screen.getByLabelText("Session name")).toHaveValue("");
    expect(screen.queryByText("Examination session created.")).not.toBeInTheDocument();
  });
});

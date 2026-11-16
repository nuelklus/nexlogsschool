import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ExaminationPanel from "./examination-panel";

const policy = {
  components: [{ key: "exam", maximum: "100.00", weight: "100.00" }],
  bands: [{ minimum: "0.00", grade: "P", description: "", passed: true }],
  decimal_places: 2, rounding: "half_up", allow_decimal_scores: true, exclude_not_applicable: false,
};
const exam = {
  id: "exam-id", name: "Term test", term: "term", school_class: "class", subject: "subject",
  configuration: "config", start_date: "2026-03-10", end_date: "2026-03-11",
  marks_deadline: "2026-03-15", publication_date: "2026-03-16", status: "draft", version: 1, published_version: 0,
};
const detail = {
  ...exam, policy, students: [{ enrollment: "enrollment", student: "student", name: "Ama Mensah", admission_number: "A1" }],
  marks: [],
  calculations: [{ enrollment: "enrollment", score: null, grade: null, description: null, passed: null, error: "Marks are incomplete." }],
  publication_history: [],
};
const props = {
  tenantId: "school", role: "teacher",
  terms: [{ id: "term", academic_year: "year", name: "Term 1", start_date: "2026-01-01", end_date: "2026-04-30", is_current: true, is_completed: false }],
  classes: [{ id: "class", academic_year: "year", name: "Basic 1", section: "A", room: "" }],
  subjects: [{ id: "subject", name: "Maths", code: "MATH" }],
};

describe("Managed examinations", () => {
  it("lists existing examinations and reloads new entries for the owner", async () => {
    let items = [exam, { ...exam, id: "session-exam", name: "Session entry" }];
    const fetch = vi.fn(async (path: string) => {
      if (path.endsWith("grading-configurations/")) return Response.json([]);
      if (path.endsWith("examinations/")) return Response.json(items);
      return Response.json({ detail: "Not found" }, { status: 404 });
    });
    render(<ExaminationPanel {...props} role="owner" authFetch={fetch} />);
    const list = within(screen.getByRole("region", { name: "Created examinations" }));
    expect(await list.findByText("2 examinations listed.")).toBeInTheDocument();
    expect(list.getAllByText("Term: Term 1 · Class: Basic 1 A · Subject: Maths")).toHaveLength(2);
    expect(list.getByText("Session entry")).toBeInTheDocument();
    expect(list.getAllByText("Dates: 2026-03-10–2026-03-11 · Status: draft · Version: 1")).toHaveLength(2);
    items = [...items, { ...exam, id: "new", name: "New examination" }];
    await userEvent.click(list.getByRole("button", { name: "Refresh examinations" }));
    expect(await list.findByText("3 examinations listed.")).toBeInTheDocument();
    expect(list.getByText("New examination")).toBeInTheDocument();
  });

  it("does not claim the list is empty when loading fails", async () => {
    const fetch = vi.fn(async (path: string) => path.endsWith("examinations/")
      ? Response.json({ detail: "Could not load examinations." }, { status: 400 })
      : Response.json([]));
    render(<ExaminationPanel {...props} role="owner" authFetch={fetch} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load examinations.");
    expect(screen.queryByText(/No managed examinations have been created/)).not.toBeInTheDocument();
  });

  it("reports when a created examination could not be refreshed without encouraging a duplicate", async () => {
    let created = false;
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith("grading-configurations/")) return Response.json([{
        id: "config", family: "family", version: 1, name: "Policy",
        class_level: null, examination_type: "term", configuration: policy,
      }]);
      if (path.endsWith("examinations/") && init?.method === "POST") {
        created = true;
        return Response.json(exam, { status: 201 });
      }
      if (path.endsWith("examinations/")) return created
        ? Response.json({ detail: "offline" }, { status: 503 }) : Response.json([]);
      return Response.json({ detail: "Not found" }, { status: 404 });
    });
    render(<ExaminationPanel {...props} role="owner" authFetch={fetch} />);
    const user = userEvent.setup();
    await screen.findByText("Policy · term · v1 · Latest");
    const summary = screen.getByText("Create examination", { selector: "summary" });
    await user.click(summary);
    const form = within(summary.parentElement!);
    await user.type(form.getByLabelText("Name"), "Term test");
    await user.selectOptions(form.getByLabelText("Term"), "term");
    await user.selectOptions(form.getByLabelText("Class"), "class");
    await user.selectOptions(form.getByLabelText("Subject"), "subject");
    await user.selectOptions(form.getByLabelText("Grading configuration"), "config");
    await user.type(form.getByLabelText("Start date"), "2026-03-10");
    await user.type(form.getByLabelText("End date"), "2026-03-11");
    await user.type(form.getByLabelText("Marks deadline"), "2026-03-15");
    await user.type(form.getByLabelText("Publication date"), "2026-03-16");
    await user.click(form.getByRole("button", { name: "Create examination" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The examination was created, but the list could not be refreshed");
    expect(form.getByLabelText("Name")).toHaveValue("");
  });

  it("loads saved grading configurations for an owner", async () => {
    const fetch = vi.fn(async (path: string) => {
      if (path.endsWith("grading-configurations/")) return Response.json([{
        id: "config", family: "family", version: 1, name: "Term 1 grading",
        class_level: null, examination_type: "End of Term", configuration: policy,
      }]);
      if (path.endsWith("examinations/")) return Response.json([]);
      return Response.json({ detail: "Not found" }, { status: 404 });
    });
    render(<ExaminationPanel {...props} role="owner" authFetch={fetch} />);
    const summary = await screen.findByText("Term 1 grading · End of Term · v1 · Latest");
    await userEvent.click(summary);
    expect(screen.getByText("exam: maximum 100.00, weight 100.00%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Term 1 grading v1" })).toBeInTheDocument();
  });

  it("requires draft changes to be saved before submission", async () => {
    let saved = false;
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith("grading-configurations/")) return Response.json([]);
      if (path.endsWith("examinations/")) return Response.json([exam]);
      if (path.endsWith("exam-id/") && init?.method === "POST") {
        expect(JSON.parse(String(init.body)).entries[0]).toMatchObject({ status: "scored", score: "75" });
        saved = true;
        return Response.json({ saved: 1 });
      }
      if (path.endsWith("exam-id/")) return Response.json({
        ...detail, marks: saved ? [{ enrollment: "enrollment", component: "exam", status: "scored", score: "75.00", remarks: "" }] : [],
      });
      if (path.endsWith("submit/")) return Response.json({ ...exam, status: "submitted" });
      return Response.json({ detail: "Not found" }, { status: 404 });
    });
    render(<ExaminationPanel {...props} authFetch={fetch} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Open Term test/ }));
    const region = await screen.findByRole("region", { name: "Open examination" });
    expect(region).toHaveFocus();
    region.scrollIntoView = vi.fn();
    await user.click(screen.getByRole("button", { name: /Open Term test/ }));
    expect(region.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(region).toHaveFocus();
    await user.selectOptions(screen.getByLabelText("Status for Ama Mensah, exam"), "scored");
    await user.type(screen.getByLabelText("Score for Ama Mensah, exam"), "75");
    expect(screen.getByRole("button", { name: "Submit saved marks" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Open Term test/ })).toBeDisabled();
    expect(screen.getByText("Save your draft marks before opening another examination.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save draft marks" }));
    expect(await screen.findByText("Draft marks saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save draft marks" })).toHaveFocus();
    expect(region.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Submit saved marks" })).toBeEnabled();
    expect(within(screen.getByRole("region", { name: "Managed examination workflow" })).getByText("Ama Mensah · A1")).toBeInTheDocument();
  });

  it("filters the accessible examination list by term, class and status", async () => {
    const fetch = vi.fn(async (path: string) => path.endsWith("examinations/")
      ? Response.json([exam, { ...exam, id: "other", name: "Other", term: "term2", school_class: "class2", status: "published" }])
      : Response.json([]));
    render(<ExaminationPanel {...props} authFetch={fetch}
      terms={[...props.terms, { ...props.terms[0], id: "term2", name: "Term 2" }]}
      classes={[...props.classes, { ...props.classes[0], id: "class2", name: "Basic 2" }]} />);
    const user = userEvent.setup();
    const list = within(screen.getByRole("region", { name: "Created examinations" }));
    await list.findByText("2 examinations listed.");
    await user.selectOptions(list.getByLabelText("Filter by term"), "term2");
    await user.selectOptions(list.getByLabelText("Filter by class"), "class2");
    await user.selectOptions(list.getByLabelText("Filter by status"), "published");
    expect(list.getByText("Other")).toBeInTheDocument();
    expect(list.queryByText("Term test")).not.toBeInTheDocument();
    expect(list.getByText("Showing 1 of 2 examinations.")).toBeInTheDocument();
    await user.selectOptions(list.getByLabelText("Filter by status"), "draft");
    expect(list.getByText("No examinations match these filters.")).toBeInTheDocument();
  });

  it("focuses examination details when opened from a session", async () => {
    const fetch = vi.fn(async (path: string) => {
      if (path.endsWith("examination-sessions/")) return Response.json([{ id: "session", name: "Session", term: "term",
        entries: [{ ...exam, class_name: "Basic 1", section: "A", subject_name: "Maths" }],
        counts: { draft: 1, submitted: 0, approved: 0, published: 0 } }]);
      if (path.endsWith("examinations/")) return Response.json([exam]);
      if (path.endsWith("exam-id/")) return Response.json(detail);
      return Response.json([]);
    });
    render(<ExaminationPanel {...props} authFetch={fetch} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Load session progress" }));
    await user.click(await screen.findByRole("button", { name: "Open Basic 1 Maths" }));
    expect(await screen.findByRole("region", { name: "Open examination" })).toHaveFocus();
    await user.selectOptions(screen.getByLabelText("Status for Ama Mensah, exam"), "scored");
    expect(screen.getByRole("button", { name: "Open Basic 1 Maths" })).toBeDisabled();
  });
});

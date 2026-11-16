import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AcademicsPage from "./academics-page";

const auth = vi.hoisted(() => ({
  authFetch: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("../auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: "head", email: "head@example.com", full_name: "Headteacher",
      memberships: [{ tenant_id: "school", tenant_name: "North Star", role: "headteacher" }],
    },
    authFetch: auth.authFetch,
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: auth.replace }) }));
vi.mock("../auth-controls", () => ({ default: () => null }));

describe("Academic Management for a headteacher", () => {
  it("loads the timetable, student attendance and term closure without management controls", async () => {
    const records: Record<string, unknown> = {
      years: [{ id: "year", name: "2026", start_date: "2026-01-01", end_date: "2026-12-31", status: "active", is_current: true }],
      terms: [{ id: "term", academic_year: "year", name: "Term 1", start_date: "2026-01-01", end_date: "2026-04-30", is_current: true, is_completed: false }],
      classes: [{ id: "class", academic_year: "year", name: "Basic 1", section: "A", room: "" }],
      subjects: [{ id: "subject", name: "Maths", code: "MATH" }],
      teachers: [{ id: "teacher", name: "Mr Mensah", email: "teacher@example.com" }],
      "teacher-assignments": [{ id: "assignment", school_class: "class", subject: "subject", teacher: "teacher" }],
      enrollments: [{ id: "enrollment", student: "student", school_class: "class", subjects: [], enrolled_on: "2026-01-01", ended_on: null, status: "active" }],
      attendance: [{ id: "attendance", enrollment: "enrollment", date: "2026-02-02", status: "present", notes: "" }],
      timetable: [{ id: "lesson", school_class: "class", subject: "subject", teacher: "teacher", weekday: 0, start_time: "08:00", end_time: "09:00", room: "" }],
      assessments: [],
      results: [],
      students: [{ id: "student", admission_number: "A1", first_name: "Ama", last_name: "Mensah", date_of_birth: null, gender: "", grade_level: "Basic 1", status: "active", enrolled_on: "2026-01-01", portal_account: false }],
    };
    auth.authFetch.mockImplementation(async (path: string) => {
      const key = path.split("/").filter(Boolean).at(-1) ?? "";
      if (key in records) return new Response(JSON.stringify(records[key]), { status: 200 });
      return new Response(JSON.stringify({ detail: "Not found." }), { status: 404 });
    });
    render(<AcademicsPage />);
    expect(await screen.findByText("2026", { selector: "strong" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Timetable" }));
    expect(screen.getByText("Monday · 08:00–09:00")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add timetable lesson" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Attendance" }));
    const history = within(screen.getByRole("region", { name: "Attendance history" }));
    await user.selectOptions(history.getByLabelText("Student"), "student");
    expect(history.getByText(/2026-02-02.*Present/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Class attendance register" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Years & terms" }));
    expect(screen.getByRole("button", { name: "Term closure" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(auth.authFetch).toHaveBeenCalledWith("/tenants/school/students/");
  });
});

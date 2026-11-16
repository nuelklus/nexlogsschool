import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AttendancePanel from "./attendance-panel";
import type { AcademicEnrollment, AcademicYear, AttendanceRecord, SchoolClass, TeachingAssignment } from "./academics-api";
import type { AuthFetch, Student } from "../school/student-api";

const years: AcademicYear[] = [
  { id: "y1", name: "2026", start_date: "2026-01-01", end_date: "2026-12-31", status: "active", is_current: true },
  { id: "y0", name: "2025", start_date: "2025-01-01", end_date: "2025-12-31", status: "active", is_current: false },
];
const classes: SchoolClass[] = [
  { id: "c1", academic_year: "y1", name: "Basic 2", section: "A", room: "" },
  { id: "c0", academic_year: "y0", name: "Basic 1", section: "", room: "" },
];
const students: Student[] = [
  { id: "s1", first_name: "Ama", last_name: "Mensah", admission_number: "A1", date_of_birth: null, gender: "", grade_level: "Basic 2", status: "active", enrolled_on: "2025-01-01", portal_account: false },
  { id: "s2", first_name: "Kojo", last_name: "Asare", admission_number: "A2", date_of_birth: null, gender: "", grade_level: "Basic 2", status: "active", enrolled_on: "2026-01-01", portal_account: false },
];
const enrollments: AcademicEnrollment[] = [
  { id: "e1", student: "s1", school_class: "c1", subjects: [], enrolled_on: "2026-01-01", ended_on: null, status: "active" },
  { id: "e2", student: "s2", school_class: "c1", subjects: [], enrolled_on: "2026-01-01", ended_on: null, status: "active" },
  { id: "e0", student: "s1", school_class: "c0", subjects: [], enrolled_on: "2025-01-01", ended_on: "2025-12-31", status: "transferred" },
];
const attendance: AttendanceRecord[] = [
  { id: "a0", enrollment: "e0", date: "2025-05-01", status: "absent", notes: "" },
  { id: "a1", enrollment: "e1", date: "2026-02-02", status: "present", notes: "On time" },
  { id: "a2", enrollment: "e2", date: "2026-02-03", status: "late", notes: "" },
];
const assignments: TeachingAssignment[] = [
  { id: "t1", teacher: "u1", school_class: "c1", subject: "subject" },
];

function setup(overrides: Partial<React.ComponentProps<typeof AttendancePanel>> = {}) {
  const authFetch = vi.fn<AuthFetch>(async () => new Response(JSON.stringify({ created: 1 }), { status: 201 }));
  const onSaved = vi.fn(async () => {});
  render(<AttendancePanel authFetch={authFetch} onSaved={onSaved} tenantId="tenant" userId="u1"
    canManage canTeach={false} years={years} classes={classes} students={students}
    enrollments={enrollments} attendance={attendance} assignments={assignments} {...overrides} />);
  return { authFetch, onSaved, user: userEvent.setup() };
}

describe("AttendancePanel", () => {
  it("filters a student's history across years, classes, dates and status", async () => {
    const { user } = setup();
    const history = within(screen.getByRole("region", { name: "Attendance history" }));
    await user.selectOptions(history.getByLabelText("Student"), "s1");
    expect(history.getByText(/2025-05-01 · Basic 1 · Absent/)).toBeInTheDocument();
    expect(history.getByText(/2026-02-02 · Basic 2 · A · Present/)).toBeInTheDocument();
    expect(history.queryByText(/2026-02-03/)).not.toBeInTheDocument();
    expect(history.getByText(/Present: 1 · Absent: 1/)).toBeInTheDocument();
    await user.selectOptions(history.getByLabelText("Academic year"), "y1");
    expect(history.queryByText(/2025-05-01/)).not.toBeInTheDocument();
    await user.selectOptions(history.getByLabelText("Class"), "c1");
    await user.type(history.getByLabelText("From date"), "2026-02-03");
    expect(history.getByText("No attendance records match these filters.")).toBeInTheDocument();
    await user.clear(history.getByLabelText("From date"));
    await user.selectOptions(history.getByLabelText("Attendance status"), "present");
    expect(history.getByText(/2026-02-02/)).toBeInTheDocument();
  });

  it("shows existing records and saves only explicitly marked students", async () => {
    const { user, authFetch, onSaved } = setup();
    const register = within(screen.getByRole("form", { name: "Class attendance register" }));
    await user.selectOptions(register.getByLabelText("Register academic year"), "y1");
    await user.selectOptions(register.getByLabelText("Register class"), "c1");
    await user.type(register.getByLabelText("Register date"), "2026-02-02");
    expect(register.getByText("Already recorded: Present · On time")).toBeInTheDocument();
    const status = register.getByLabelText("Status for Kojo Asare · A2");
    expect(status).toHaveValue("");
    await user.selectOptions(status, "absent");
    await user.type(register.getByLabelText("Notes for Kojo Asare · A2"), "Sick");
    await user.click(register.getByRole("button", { name: "Save class attendance" }));
    expect(await register.findByText(/Saved attendance for 1 student/)).toBeInTheDocument();
    expect(JSON.parse(String(authFetch.mock.calls[0][1]?.body))).toEqual({
      school_class: "c1", date: "2026-02-02",
      entries: [{ enrollment: "e2", status: "absent", notes: "Sick" }],
    });
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it("only offers assigned classes to teachers and surfaces conflicts", async () => {
    const authFetch = vi.fn(async () => new Response(JSON.stringify({ detail: "Attendance was already recorded. Reload." }), { status: 409 }));
    const { user, onSaved } = setup({ canManage: false, canTeach: true, authFetch });
    const register = within(screen.getByRole("form", { name: "Class attendance register" }));
    await user.selectOptions(register.getByLabelText("Register academic year"), "y1");
    expect(register.getByRole("option", { name: "Basic 2 · A" })).toBeInTheDocument();
    await user.selectOptions(register.getByLabelText("Register class"), "c1");
    await user.type(register.getByLabelText("Register date"), "2026-02-02");
    await user.selectOptions(register.getByLabelText("Status for Kojo Asare · A2"), "present");
    await user.click(register.getByRole("button", { name: "Save class attendance" }));
    expect(await register.findByRole("alert")).toHaveTextContent("already recorded");
    await user.click(register.getByRole("button", { name: "Reload register" }));
    expect(onSaved).toHaveBeenCalledOnce();
    expect(register.getByLabelText("Status for Kojo Asare · A2")).toHaveValue("");
  });

  it("does not submit a register with unmarked students or expose it to readers", async () => {
    const { user, authFetch } = setup();
    const register = within(screen.getByRole("form", { name: "Class attendance register" }));
    await user.selectOptions(register.getByLabelText("Register academic year"), "y1");
    await user.selectOptions(register.getByLabelText("Register class"), "c1");
    await user.type(register.getByLabelText("Register date"), "2026-02-02");
    await user.click(register.getByRole("button", { name: "Save class attendance" }));
    expect(authFetch).not.toHaveBeenCalled();
  });

  it("lets read-only roles view history without the register", () => {
    setup({ canManage: false, canTeach: false });
    expect(screen.queryByRole("form", { name: "Class attendance register" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Attendance history" })).toBeInTheDocument();
  });
});

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassEnrollmentForm from "./class-enrollment-form";

const student = { id: "s", admission_number: "A-001", first_name: "Ama", last_name: "Mensah",
  date_of_birth: null, gender: "", grade_level: "Basic 1", status: "active" as const, enrolled_on: "2026-01-01", portal_account: false };
const schoolClass = { id: "c", academic_year: "y", name: "Basic 1", section: "A", room: "" };
const year = { id: "y", name: "2026", start_date: "2026-01-01", end_date: "2026-12-31", status: "active", is_current: true };
const enrollment = { id: "e", student: "s", school_class: "c", subjects: [], enrolled_on: "2026-01-01", status: "active" as const };
const props = {
  students: [student], classes: [schoolClass], years: [year],
  subjects: [{ id: "m", name: "Mathematics", code: "MATH" }],
  assignments: [{ id: "a", school_class: "c", subject: "m", teacher: "t" }],
};

describe("ClassEnrollmentForm", () => {
  it("finds an existing enrollment and shows class subjects without creating a duplicate", async () => {
    const onCreate = vi.fn();
    render(<ClassEnrollmentForm {...props} students={[student, { ...student, id: "s2", admission_number: "A-002", first_name: "Kojo" }]}
      enrollments={[enrollment]} onCreate={onCreate} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search student by name or admission number"), "A-001");
    expect(within(screen.getByLabelText("Choose student")).queryByRole("option", { name: /Kojo/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Choose student"), "s");
    await user.selectOptions(screen.getByLabelText("Class"), "c");
    expect(screen.getByText("This student is already enrolled in this class.")).toBeInTheDocument();
    expect(screen.getByText("Class subjects: Mathematics")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create enrollment" })).toBeDisabled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("creates a missing enrollment without a subject restriction", async () => {
    const onCreate = vi.fn(async () => {});
    render(<ClassEnrollmentForm {...props} enrollments={[]} onCreate={onCreate} />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Choose student"), "s");
    await user.selectOptions(screen.getByLabelText("Class"), "c");
    await user.type(screen.getByLabelText("Enrollment date"), "2026-01-05");
    await user.click(screen.getByRole("button", { name: "Create enrollment" }));
    expect(onCreate).toHaveBeenCalledWith({ student: "s", school_class: "c", enrolled_on: "2026-01-05" });
    expect(await within(screen.getByRole("form", { name: "Manage student class enrollment" })).findByText(/Enrollment created/)).toBeInTheDocument();
  });
});

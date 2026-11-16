import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AddStudentPage from "./add-student-page";
import type { AuthUser } from "./auth-provider";

const auth = vi.hoisted(() => ({
  value: {} as { user: AuthUser | null; isLoading: boolean; authFetch: ReturnType<typeof vi.fn> },
  replace: vi.fn(),
}));

vi.mock("./auth-provider", () => ({ useAuth: () => auth.value }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: auth.replace, push: vi.fn() }) }));

const student = {
  id: "s1",
  admission_number: "A-001",
  first_name: "Esi",
  last_name: "Boateng",
  date_of_birth: null,
  gender: "",
  grade_level: "Class 3",
  status: "active",
  enrolled_on: "2026-10-25",
  portal_account: false,
};

const year = {
  id: "y1",
  name: "2026/2027",
  start_date: "2026-09-01",
  end_date: "2027-07-31",
  status: "active",
  is_current: true,
};

const schoolClass = {
  id: "c1",
  academic_year: "y1",
  campus: null,
  name: "Custom class",
  section: "",
  room: "",
  class_level: null,
  class_teacher: null,
  is_active: true,
};

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

function signInAs(role: string, handler?: (path: string, init?: RequestInit) => Promise<Response>) {
  const authFetch = vi.fn((path: string, init?: RequestInit) => {
    if (path.endsWith("/academics/years/")) return json([year]);
    if (path.endsWith("/academics/classes/")) return json([schoolClass]);
    if (path.endsWith("/finance/campuses/")) return json([]);
    return (handler ?? (() => json([student])))(path, init);
  });
  auth.value = {
    user: {
      id: "u1",
      email: "u@example.com",
      full_name: "Test User",
      memberships: [{ tenant_id: "t1", tenant_name: "North Star Academy", role }],
    },
    isLoading: false,
    authFetch,
  };
  return authFetch;
}

beforeEach(() => {
  auth.replace.mockClear();
  auth.value = { user: null, isLoading: false, authFetch: vi.fn() };
});

describe("AddStudentPage", () => {
  it.each(["owner", "school_admin"])("shows the enrolment page to a %s", async (role) => {
    signInAs(role);
    render(<AddStudentPage />);

    expect(screen.getByRole("heading", { name: "Students" })).toBeInTheDocument();
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Enrol a student" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Subscription" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Students/ }).some((a) => a.getAttribute("href") === "/dashboard-add-student")).toBe(true);
  });

  it.each(["teacher", "accountant", "parent", "student"])(
    "sends a %s back to the root and shows nothing",
    (role) => {
      const authFetch = signInAs(role);
      render(<AddStudentPage />);
      expect(auth.replace).toHaveBeenCalledWith("/");
      expect(screen.queryByRole("form", { name: "Enrol a student" })).not.toBeInTheDocument();
      expect(authFetch).not.toHaveBeenCalled();
    },
  );

  it("enrols a student and adds them to the roster", async () => {
    const authFetch = signInAs("owner", (_path, init) =>
      init?.method === "POST" ? json({ ...student, id: "s2", first_name: "Kojo", last_name: "Asare", admission_number: "ASHS-2026-0002" }, 201) : json([student]),
    );
    render(<AddStudentPage />);
    await screen.findByText("Esi Boateng");

    const user = userEvent.setup();
    expect(screen.queryByLabelText("Admission number")).not.toBeInTheDocument();
    expect(screen.getByText("Generated automatically")).toBeInTheDocument();
    await user.type(screen.getByLabelText("First name"), "Kojo");
    await user.type(screen.getByLabelText("Last name"), "Asare");
    await screen.findByRole("option", { name: "2026/2027" });
    await user.selectOptions(screen.getByLabelText("Class group"), "c1");
    await user.type(screen.getByLabelText("Enrollment date"), "2026-09-10");
    await user.click(screen.getByRole("button", { name: "Enrol student" }));

    expect(
      await screen.findByText("Kojo Asare has been enrolled. Admission number: ASHS-2026-0002"),
    ).toBeInTheDocument();
    expect(screen.getByText("Kojo Asare")).toBeInTheDocument();
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/t1/students/");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      first_name: "Kojo",
      last_name: "Asare",
      enrolled_on: "2026-09-10",
      initial_academic_year: "y1",
      initial_school_class: "c1",
    });
  });

  it("shows enrolment errors from the server", async () => {
    signInAs("owner", (_path, init) =>
      init?.method === "POST"
        ? json({ detail: "The admission number could not be reserved. Please try again." }, 409)
        : json([]),
    );
    render(<AddStudentPage />);
    await screen.findByText(/No students are enrolled yet/);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("First name"), "Kojo");
    await user.type(screen.getByLabelText("Last name"), "Asare");
    await user.selectOptions(screen.getByLabelText("Class group"), "c1");
    await user.type(screen.getByLabelText("Enrollment date"), "2026-09-10");
    await user.click(screen.getByRole("button", { name: "Enrol student" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be reserved");
  });

  it("filters the roster with the search box", async () => {
    signInAs("owner", () => json([student, { ...student, id: "s2", first_name: "Kojo", last_name: "Asare", admission_number: "A-002" }]));
    render(<AddStudentPage />);
    await screen.findByText("Esi Boateng");

    await userEvent.setup().type(screen.getByLabelText("Search students"), "kojo");
    expect(screen.queryByText("Esi Boateng")).not.toBeInTheDocument();
    expect(screen.getByText("Kojo Asare")).toBeInTheDocument();

    await userEvent.setup().type(screen.getByLabelText("Search students"), "zzz");
    expect(screen.getByText("No students match your search or filters.")).toBeInTheDocument();
  });

  it("opens a student's details from the roster and restores focus on close", async () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
    Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } });
    signInAs("owner", (path) => path.includes("/finance/students/") ? json({ outstanding_invoices: "0.00", advance_credit: "0.00" })
      : path.includes("/finance/invoices/") ? json([]) : json([student]));
    render(<AddStudentPage />);
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "View details for Esi Boateng, A-001" });
    await user.click(button);
    expect(await screen.findByRole("dialog", { name: "Esi Boateng" })).toBeInTheDocument();
    expect(await screen.findByText(/No non-void invoices found among the 500/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
  });

  it("combines class group, status, gender and portal filters and clears them", async () => {
    signInAs("owner", () => json([
      { ...student, current_class: "c1", gender: "Female", portal_account: true },
      { ...student, id: "s2", first_name: "Kojo", current_class: null, gender: "Male", status: "graduated" },
    ]));
    render(<AddStudentPage />);
    const user = userEvent.setup();
    await screen.findByText("Esi Boateng");
    const roster = within(screen.getByRole("region", { name: "Student roster" }));
    await roster.findByRole("option", { name: "Custom class" });
    await user.selectOptions(roster.getByLabelText("Filter by class group"), "c1");
    await user.selectOptions(roster.getByLabelText("Filter by status"), "active");
    await user.selectOptions(roster.getByLabelText("Filter by gender"), "Female");
    await user.selectOptions(roster.getByLabelText("Filter by portal access"), "active");
    expect(roster.getByText("Esi Boateng")).toBeInTheDocument();
    expect(roster.queryByText("Kojo Boateng")).not.toBeInTheDocument();
    expect(roster.getByText("Showing 1 of 2 students.")).toBeInTheDocument();
    await user.selectOptions(roster.getByLabelText("Filter by class group"), "__none__");
    expect(roster.getByText("No students match your search or filters.")).toBeInTheDocument();
    await user.click(roster.getByRole("button", { name: "Clear filters" }));
    expect(roster.getByText("Showing 2 of 2 students.")).toBeInTheDocument();
    expect(roster.getByText("Kojo Boateng")).toBeInTheDocument();
  });

  it("filters by campus and retains students without one", async () => {
    signInAs("owner", () => json([
      { ...student, campus: "campus1" },
      { ...student, id: "s2", first_name: "Kojo", campus: null },
    ]));
    auth.value.authFetch.mockImplementation((path: string) =>
      path.endsWith("/finance/campuses/") ? json([{ id: "campus1", name: "Main", code: "M", status: "active" }])
        : path.endsWith("/academics/years/") ? json([year])
        : path.endsWith("/academics/classes/") ? json([schoolClass])
        : json([{ ...student, campus: "campus1" }, { ...student, id: "s2", first_name: "Kojo", campus: null }]));
    render(<AddStudentPage />);
    const user = userEvent.setup();
    const roster = within(screen.getByRole("region", { name: "Student roster" }));
    await roster.findByRole("option", { name: "Main" });
    await user.selectOptions(roster.getByLabelText("Filter by campus"), "campus1");
    expect(roster.getByText("Esi Boateng")).toBeInTheDocument();
    expect(roster.queryByText("Kojo Boateng")).not.toBeInTheDocument();
    await user.selectOptions(roster.getByLabelText("Filter by campus"), "__none__");
    expect(roster.getByText("Kojo Boateng")).toBeInTheDocument();
  });

  it("distinguishes class sections with the same class name", async () => {
    signInAs("owner", () => json([
      { ...student, current_class: "c1", grade_level: "Basic 1" },
      { ...student, id: "s2", first_name: "Kojo", current_class: "c2", grade_level: "Basic 1" },
    ]));
    auth.value.authFetch.mockImplementation((path: string) =>
      path.endsWith("/finance/campuses/") ? json([])
        : path.endsWith("/academics/years/") ? json([year])
        : path.endsWith("/academics/classes/") ? json([
          { ...schoolClass, name: "Basic 1", section: "A" },
          { ...schoolClass, id: "c2", name: "Basic 1", section: "B" },
        ]) : json([
          { ...student, current_class: "c1", grade_level: "Basic 1" },
          { ...student, id: "s2", first_name: "Kojo", current_class: "c2", grade_level: "Basic 1" },
        ]));
    render(<AddStudentPage />);
    const roster = within(screen.getByRole("region", { name: "Student roster" }));
    await roster.findByRole("option", { name: "Basic 1 B" });
    await userEvent.selectOptions(roster.getByLabelText("Filter by class group"), "c2");
    expect(roster.getByText("Kojo Boateng")).toBeInTheDocument();
    expect(roster.queryByText("Esi Boateng")).not.toBeInTheDocument();
    expect(roster.getByText("A-001 · Basic 1 B")).toBeInTheDocument();
  });

  it("reports unavailable filter options without hiding the student list", async () => {
    signInAs("owner", () => json([student]));
    auth.value.authFetch.mockImplementation((path: string) =>
      path.endsWith("/finance/campuses/") ? json({ detail: "Campus service unavailable" }, 503)
        : path.endsWith("/academics/years/") ? json([year])
        : path.endsWith("/academics/classes/") ? json([schoolClass])
        : json([student]));
    render(<AddStudentPage />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(await screen.findByText(/Class and campus filter options could not be loaded/)).toHaveTextContent("Campus service unavailable");
    const roster = within(screen.getByRole("region", { name: "Student roster" }));
    expect(roster.getByLabelText("Filter by class group")).toBeDisabled();
    expect(roster.getByLabelText("Filter by status")).toBeEnabled();
  });

  it("finds a student by admission number and displays it in the roster", async () => {
    signInAs("owner", () => json([student, { ...student, id: "s2", first_name: "Kojo", last_name: "Asare", admission_number: "ASHS-2026-0002" }]));
    render(<AddStudentPage />);
    await screen.findByText("Esi Boateng");
    expect(screen.getByText(/ASHS-2026-0002/)).toBeInTheDocument();

    await userEvent.setup().type(screen.getByLabelText("Search students"), "2026-0002");
    expect(screen.queryByText("Esi Boateng")).not.toBeInTheDocument();
    expect(screen.getByText("Kojo Asare")).toBeInTheDocument();
  });

  it("sends a guardian invitation from a student row", async () => {
    const authFetch = signInAs("owner", (_path, init) =>
      init?.method === "POST" ? json({ status: "invited", email: "yaa@example.com" }, 201) : json([student]),
    );
    render(<AddStudentPage />);
    await screen.findByText("Esi Boateng");

    const user = userEvent.setup();
    await user.click(screen.getByText("Invite family or student"));
    await user.type(screen.getByLabelText("Invite guardian email"), "yaa@example.com");
    await user.click(screen.getByRole("button", { name: "Invite guardian" }));

    expect(await screen.findByText("Invitation sent to yaa@example.com.")).toBeInTheDocument();
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/t1/students/s1/guardians/");
  });

  it("explains an expired subscription instead of showing the roster", async () => {
    signInAs("owner", (path) =>
      path.endsWith("/subscription/")
        ? json({
            status: "expired",
            has_access: false,
            plan: null,
            started_at: null,
            current_period_start: null,
            expires_at: null,
            renewal: { instructions: "", support_email: "" },
          })
        : json({ detail: "This school's subscription has expired." }, 402),
    );
    render(<AddStudentPage />);

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("subscription has expired");
    expect(within(document.body).queryByRole("form", { name: "Enrol a student" })).not.toBeInTheDocument();
  });
});

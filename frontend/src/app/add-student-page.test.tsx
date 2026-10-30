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

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

function signInAs(role: string, handler?: (path: string, init?: RequestInit) => Promise<Response>) {
  const authFetch = vi.fn(handler ?? (() => json([student])));
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
    expect(screen.getByText("No students match your search.")).toBeInTheDocument();
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

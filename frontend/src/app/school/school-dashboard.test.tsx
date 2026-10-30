import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../auth-provider";
import SchoolDashboard from "./school-dashboard";

const auth = vi.hoisted(() => ({
  value: {} as {
    user: AuthUser | null;
    isLoading: boolean;
    authFetch: ReturnType<typeof vi.fn>;
  },
}));

vi.mock("../auth-provider", () => ({ useAuth: () => auth.value }));

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

const subscription = {
  status: "active",
  has_access: true,
  plan: {
    code: "termly",
    name: "Termly",
    is_trial: false,
    billing_interval: "termly",
    price: "300.00",
    currency: "GHS",
  },
  started_at: "2026-10-26T09:00:00Z",
  current_period_start: "2026-10-26T09:00:00Z",
  expires_at: "2027-01-26T09:00:00Z",
  renewal: { instructions: "Pay by mobile money.", support_email: "billing@nlschool.example" },
};

function signInAs(role: string, authFetch = vi.fn()) {
  auth.value = {
    user: {
      id: "u1",
      email: "u@example.com",
      full_name: "Test User",
      memberships: [{ tenant_id: "t1", tenant_name: "North Star", role }],
    },
    isLoading: false,
    authFetch,
  };
  return authFetch;
}

beforeEach(() => {
  auth.value = { user: null, isLoading: false, authFetch: vi.fn() };
});

describe("SchoolDashboard", () => {
  it("asks anonymous visitors to sign in", () => {
    render(<SchoolDashboard />);
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
  });

  it("lets an owner see students and enroll a new one", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return json({ ...student, id: "s2", first_name: "Kojo", admission_number: "ASHS-2026-0002" }, 201);
      }
      return json([student]);
    });
    render(<SchoolDashboard />);

    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    const user = userEvent.setup();
    expect(screen.queryByLabelText("Admission number")).not.toBeInTheDocument();
    expect(screen.getByText(/generated automatically/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("First name"), "Kojo");
    await user.type(screen.getByLabelText("Last name"), "Asare");
    await user.click(screen.getByRole("button", { name: "Enroll student" }));

    expect(await screen.findByText("Kojo Boateng")).toBeInTheDocument();
    expect(await screen.findByText(/Admission number: ASHS-2026-0002/)).toBeInTheDocument();
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/t1/students/");
    expect(JSON.parse(post?.[1].body)).toEqual({
      first_name: "Kojo",
      last_name: "Asare",
    });
  });

  it("shows backend enrollment errors", async () => {
    const authFetch = signInAs("school_admin");
    authFetch.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? json({ detail: "The admission number could not be reserved. Please try again." }, 409)
        : json([]),
    );
    render(<SchoolDashboard />);
    await screen.findByText("No students are enrolled yet.");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("First name"), "Kojo");
    await user.type(screen.getByLabelText("Last name"), "Asare");
    await user.click(screen.getByRole("button", { name: "Enroll student" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be reserved");
  });

  it("sends a guardian invitation for a student", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? json({ status: "invited", email: "yaa@example.com" }, 201)
        : json([student]),
    );
    render(<SchoolDashboard />);
    await screen.findByText("Esi Boateng");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Invite guardian email"), "yaa@example.com");
    await user.type(screen.getByLabelText("Invite guardian relationship"), "Mother");
    await user.click(screen.getByRole("button", { name: "Invite guardian" }));

    expect(await screen.findByText("Invitation sent to yaa@example.com.")).toBeInTheDocument();
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/t1/students/s1/guardians/");
    expect(JSON.parse(post?.[1].body)).toEqual({
      email: "yaa@example.com",
      relationship: "Mother",
      is_primary: false,
    });
  });

  it("shows the subscription-expired message when the school's plan has lapsed", async () => {
    signInAs(
      "owner",
      vi.fn(() =>
        json({ detail: "This school's subscription has expired. Contact NLSchool to renew it." }, 402),
      ),
    );
    render(<SchoolDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("subscription has expired");
  });

  it("explains an expired subscription to an administrator and keeps billing info", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((path: string) =>
      path.endsWith("/subscription/")
        ? json({ ...subscription, status: "expired", has_access: false })
        : json({ detail: "This school's subscription has expired. Contact NLSchool to renew it." }, 402),
    );
    render(<SchoolDashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("subscription has expired");
    expect(await screen.findByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("How to renew")).toBeInTheDocument();
    expect(screen.queryByText("Enroll a student")).not.toBeInTheDocument();
    expect(screen.queryByText("Students")).not.toBeInTheDocument();
  });

  it("tells teachers, parents and students to ask their administrator, with no billing call", async () => {
    for (const role of ["teacher", "parent", "student"]) {
      const authFetch = signInAs(
        role,
        vi.fn(() => json({ detail: "This school's subscription has expired." }, 402)),
      );
      const { unmount } = render(<SchoolDashboard />);
      expect(await screen.findByRole("alert")).toHaveTextContent("ask your school administrator");
      expect(screen.queryByText("How to renew")).not.toBeInTheDocument();
      expect(authFetch).not.toHaveBeenCalledWith("/tenants/t1/subscription/");
      unmount();
    }
  });

  it("gives teachers a read-only list", async () => {
    const authFetch = signInAs("teacher", vi.fn(() => json([student])));
    render(<SchoolDashboard />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.queryByText("Enroll a student")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite guardian" })).not.toBeInTheDocument();
    expect(authFetch).toHaveBeenCalledTimes(1);
  });

  it("shows a parent only their linked children", async () => {
    const authFetch = signInAs(
      "parent",
      vi.fn(() => json([{ ...student, relationship: "Mother", is_primary: true }])),
    );
    render(<SchoolDashboard />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.getByText(/primary guardian/)).toBeInTheDocument();
    expect(authFetch).toHaveBeenCalledWith("/tenants/t1/me/children/");
    expect(screen.queryByText("Enroll a student")).not.toBeInTheDocument();
  });

  it("shows a student their own profile", async () => {
    const authFetch = signInAs("student", vi.fn(() => json(student)));
    render(<SchoolDashboard />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    await waitFor(() => expect(authFetch).toHaveBeenCalledWith("/tenants/t1/me/student/"));
  });
});

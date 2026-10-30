import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "./auth-provider";
import WorkspaceHome from "./workspace-home";

const auth = vi.hoisted(() => ({
  value: {} as { user: AuthUser | null; isLoading: boolean; authFetch: ReturnType<typeof vi.fn> },
}));

vi.mock("./auth-provider", () => ({ useAuth: () => auth.value }));

const student = {
  id: "s1",
  admission_number: "A-001",
  first_name: "Esi",
  last_name: "Boateng",
  date_of_birth: null,
  gender: "",
  grade_level: "",
  status: "active",
  enrolled_on: "2026-10-25",
  portal_account: false,
};

const subscription = {
  status: "trialing",
  has_access: true,
  plan: { code: "trial", name: "Free trial", is_trial: true, billing_interval: null, price: "0.00", currency: "GHS" },
  started_at: "2026-10-26T09:00:00Z",
  current_period_start: null,
  expires_at: "2026-11-25T09:00:00Z",
  renewal: { instructions: "", support_email: "" },
};

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

function signInAs(role: string, students: unknown[] = [student]) {
  const authFetch = vi.fn((path: string, init?: RequestInit) => {
    if (path.endsWith("/invitations/") && init?.method === "POST") {
      return json({ id: "i1", email: "kofi@example.com", role: "accountant", expires_at: "2026-11-02T09:00:00Z" }, 201);
    }
    return path.endsWith("/subscription/") ? json(subscription) : json(students);
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
  auth.value = { user: null, isLoading: false, authFetch: vi.fn() };
});

describe("WorkspaceHome", () => {
  it.each(["owner", "school_admin"])("shows the setup workspace to a %s", async (role) => {
    signInAs(role);
    render(<WorkspaceHome />);

    expect(screen.getByRole("heading", { name: "North Star Academy" })).toBeInTheDocument();
    expect(await screen.findByText("1 enrolled")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Invite staff" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Subscription" })).toBeInTheDocument();
    expect(await screen.findByText("Free trial", { selector: "strong" })).toBeInTheDocument();
  });

  it.each(["accountant", "teacher"])("does not show the workspace to a %s", async (role) => {
    signInAs(role);
    render(<WorkspaceHome />);

    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Invite staff" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main navigation" })).not.toBeInTheDocument();
  });

  it.each(["parent", "student"])("keeps a %s on their own view", (role) => {
    signInAs(role);
    render(<WorkspaceHome />);
    expect(screen.queryByRole("region", { name: "Invite staff" })).not.toBeInTheDocument();
  });

  it("points an owner with no students at enrolling the first one", async () => {
    signInAs("owner", []);
    render(<WorkspaceHome />);
    expect(await screen.findByRole("heading", { name: "Enrol your first student" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Enrol students/ })).toHaveAttribute("href", "/school");
  });

  it("lets an owner invite staff", async () => {
    const authFetch = signInAs("owner");
    render(<WorkspaceHome />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Staff email"), "kofi@example.com");
    await user.selectOptions(screen.getByLabelText("Staff role"), "accountant");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));

    expect(await screen.findByText(/Invitation sent to kofi@example.com/)).toBeInTheDocument();
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/t1/invitations/");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({ email: "kofi@example.com", role: "accountant" });
  });

  it("shows invitation errors from the server", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? json({ detail: "A current invitation already exists for this email." }, 409)
        : path.endsWith("/subscription/") ? json(subscription) : json([student]),
    );
    render(<WorkspaceHome />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Staff email"), "kofi@example.com");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    const form = screen.getByRole("region", { name: "Invite staff" });
    expect(await within(form).findByRole("alert")).toHaveTextContent("already exists");
  });

  it("tells an owner to renew when the subscription has lapsed", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((path: string) =>
      path.endsWith("/subscription/")
        ? json({ ...subscription, status: "expired", has_access: false })
        : json({ detail: "This school's subscription has expired." }, 402),
    );
    render(<WorkspaceHome />);
    expect(await screen.findByRole("heading", { name: "Renew your subscription" })).toBeInTheDocument();
    expect(screen.getByText("Unavailable until the subscription is renewed")).toBeInTheDocument();
  });

  it("shows a termly plan with its price, expiry and renewal instructions", async () => {
    const authFetch = signInAs("owner");
    const termly = {
      status: "active",
      has_access: true,
      plan: { code: "termly", name: "Termly", is_trial: false, billing_interval: "termly", price: "300.00", currency: "GHS" },
      started_at: "2026-10-26T09:00:00Z",
      current_period_start: "2026-10-26T09:00:00Z",
      expires_at: "2027-01-26T09:00:00Z",
      renewal: { instructions: "Pay by mobile money.", support_email: "billing@nlschool.example" },
    };
    authFetch.mockImplementation((path: string) => (path.endsWith("/subscription/") ? json(termly) : json([student])));
    render(<WorkspaceHome />);

    const panel = screen.getByRole("region", { name: "Subscription" });
    expect(await within(panel).findByText("Termly (every 3 months)")).toBeInTheDocument();
    expect(within(panel).getByText("GHS 300.00")).toBeInTheDocument();
    expect(within(panel).getByText("26 Jan 2027")).toBeInTheDocument();
    expect(within(panel).getByText("Pay by mobile money.")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "billing@nlschool.example" })).toHaveAttribute(
      "href",
      "mailto:billing@nlschool.example",
    );
  });

  it("shows a trial as free with no billing interval", async () => {
    signInAs("owner");
    render(<WorkspaceHome />);

    const panel = screen.getByRole("region", { name: "Subscription" });
    expect(await within(panel).findByText("Free")).toBeInTheDocument();
    expect(within(panel).getByText("Trial ends")).toBeInTheDocument();
    expect(within(panel).queryByText("Billing")).not.toBeInTheDocument();
  });

  it("copes when subscription details cannot be loaded", async () => {
    const authFetch = signInAs("owner");
    authFetch.mockImplementation((path: string) =>
      path.endsWith("/subscription/") ? json({ detail: "boom" }, 500) : json([student]),
    );
    render(<WorkspaceHome />);
    expect(await screen.findByText("Subscription details are unavailable right now.")).toBeInTheDocument();
  });
});

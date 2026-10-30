import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../auth-provider";
import BillingPage from "./billing-page";
import InvoicesPage from "./invoices-page";
import ServicesPage from "./services-page";

const auth = vi.hoisted(() => ({
  value: {} as { user: AuthUser | null; isLoading: boolean; authFetch: ReturnType<typeof vi.fn> },
  replace: vi.fn(),
}));

vi.mock("../auth-provider", () => ({ useAuth: () => auth.value }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: auth.replace, push: vi.fn() }),
}));

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

const years = [{ id: "y1", name: "2026/2027", is_current: true }];
const students = [
  {
    id: "s1", first_name: "Esi", last_name: "Boateng", admission_number: "S-0001", status: "active", grade_level: "JHS 1",
    date_of_birth: null, gender: "female", enrolled_on: "2026-09-01", portal_account: false,
  },
];
const categories = [
  { id: "c1", code: "tuition", name: "Tuition", is_active: true },
  { id: "c2", code: "lunch", name: "Lunch", is_active: true },
];
const previewBody = {
  ready: 1,
  blocked: 0,
  total: "1000.00",
  students: [
    {
      student: "s1",
      student_name: "Esi Boateng",
      admission_number: "S-0001",
      grade_level: "JHS 1",
      issues: [],
      can_generate: true,
      totals: { subtotal: "1000.00", discount_total: "0.00", total: "1000.00" },
      calculation: {
        calculated_on: "2026-10-26",
        fees: [{ description: "Tuition", amount: "1000.00", applicability: "compulsory", quantity: null, unit_price: null }],
        charges: [],
        discounts: [],
        excluded: [{ description: "Lunch", applicability: "optional", code: "not_subscribed", reason: "Not subscribed to Lunch." }],
      },
    },
  ],
};

function signInAs(role: string, handler: (path: string, init?: RequestInit) => Promise<Response>) {
  const authFetch = vi.fn(handler);
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

const route = (path: string) => {
  if (path.includes("/students/")) return json(students);
  if (path.includes("academic-years/")) return json(years);
  if (path.includes("fee-categories/")) return json(categories);
  if (path.includes("fee-schedules/")) return json([]);
  if (path.includes("invoices/preview/")) return json(previewBody);
  if (path.includes("invoices/")) return json([]);
  return json([]);
};

beforeEach(() => {
  auth.replace.mockClear();
  auth.value = { user: null, isLoading: false, authFetch: vi.fn() };
});

describe("services page", () => {
  it.each(["teacher", "parent", "student"])("redirects a %s without calling the API", async (role) => {
    const authFetch = signInAs(role, () => json([]));
    render(<ServicesPage />);
    await waitFor(() => expect(auth.replace).toHaveBeenCalledWith("/"));
    expect(authFetch).not.toHaveBeenCalled();
  });

  it("will not submit an adjustment without a reason", async () => {
    const authFetch = signInAs("accountant", (path) => route(path));
    render(<ServicesPage />);
    const form = await screen.findByRole("form", { name: "Add adjustment" });
    await screen.findAllByRole("option", { name: /Esi Boateng/ });
    const user = userEvent.setup();
    await user.selectOptions(form.querySelector("select[name=student]")!, "s1");
    await user.selectOptions(form.querySelector("select[name=category]")!, "c1");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A reason is required for every adjustment.")).toBeInTheDocument();
    expect(authFetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });
});

describe("invoice preview", () => {
  it("shows included fees, excluded optional fees with the reason, and the total", async () => {
    const authFetch = signInAs("accountant", (path) => route(path));
    render(<BillingPage />);
    const section = await screen.findByRole("region", { name: "Bulk billing" });
    const user = userEvent.setup();
    const selects = section.querySelectorAll("select");
    await user.selectOptions(selects[1], "Term 1");
    await user.selectOptions(selects[2], "JHS 1");
    await user.click(screen.getByRole("button", { name: "Preview invoices" }));

    expect(await screen.findByText(/Not subscribed to Lunch\./)).toBeInTheDocument();
    expect(screen.getByText("Tuition: GHS 1,000.00")).toBeInTheDocument();
    expect(screen.getByText(/1 ready · 0 blocked · Total GHS 1,000.00/)).toBeInTheDocument();
    const call = authFetch.mock.calls.find(([path]) => String(path).includes("invoices/preview/"));
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({ period_label: "Term 1", grade_level: "JHS 1" });
    expect(screen.queryByRole("button", { name: /Issue/ })).not.toBeInTheDocument();
  });
});

describe("InvoicesPage", () => {
  it("lists invoices separately and allows drafts to be issued", async () => {
    const invoice = {
      id: "i1",
      number: "INV-000001",
      status: "draft",
      overdue: false,
      student: "s1",
      student_name: "Esi Boateng",
      admission_number: "S-0001",
      grade_level: "JHS 1",
      academic_year: "y1",
      academic_year_name: "2026/2027",
      period_label: "Term 1",
      issue_date: null,
      due_date: "2026-10-30",
      subtotal: "1000.00",
      discount_total: "0.00",
      total: "1000.00",
      amount_paid: "0.00",
      credited_total: "0.00",
      balance: "1000.00",
    };
    const authFetch = signInAs("accountant", (path, init) => {
      if (path.includes("invoices/") && init?.method === "POST") return json(invoice);
      if (path.includes("invoices/")) return json([invoice]);
      return route(path);
    });
    const user = userEvent.setup();
    render(<InvoicesPage />);

    expect(await screen.findByRole("link", { name: "INV-000001" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Issue" }));
    await waitFor(() => expect(authFetch).toHaveBeenCalledWith(
      expect.stringContaining("/invoices/i1/issue/"),
      expect.objectContaining({ method: "POST" }),
    ));
  });
});

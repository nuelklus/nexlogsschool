import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../auth-provider";
import AdvancesPage from "./advances-page";
import ArrearsPage from "./arrears-page";
import FinanceDashboardPage from "./dashboard-page";
import FinanceSettingsPage from "./finance-settings-page";
import { formatMoney } from "./finance-api";
import PaymentsPage from "./payments-page";
import FinanceRemindersPage from "./reminders-page";
import FinanceReportsPage from "./reports-page";
import ReceiptPage from "./receipt-page";

const auth = vi.hoisted(() => ({
  value: {} as { user: AuthUser | null; isLoading: boolean; authFetch: ReturnType<typeof vi.fn> },
  replace: vi.fn(),
}));

vi.mock("../auth-provider", () => ({ useAuth: () => auth.value }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: auth.replace, push: vi.fn() }),
  useParams: () => ({ id: "p1" }),
}));

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

const summary = {
  total_invoiced: "5000.00",
  total_discounts: "0.00",
  total_collected: "1200.00",
  pending_unconfirmed: "300.00",
  outstanding_balance: "3800.00",
  overdue_balance: "0.00",
  overdue_invoices: 0,
  student_advances_held: "0.00",
  collections_by_method: [],
  collections_by_campus: [],
  collections_by_class: [],
  collections_by_date: [],
  basis: {},
};

const methods = [
  { id: "m1", name: "Cash", kind: "cash", is_active: true },
  { id: "m2", name: "Mobile money", kind: "mobile_money", is_active: true },
];

const payment = {
  id: "p1",
  student: "s1",
  student_name: "Esi Boateng",
  method: "m2",
  method_name: "Mobile money",
  method_kind: "mobile_money",
  amount: "300.00",
  payment_date: "2026-10-26",
  reference: "MM123",
  payer_name: "",
  status: "pending",
  receipt_number: "",
  allocated_total: "0.00",
  refunded_total: "0.00",
  unallocated: "0.00",
  reversal_reason: "",
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

beforeEach(() => {
  auth.replace.mockClear();
  auth.value = { user: null, isLoading: false, authFetch: vi.fn() };
});

describe("formatMoney", () => {
  it("formats decimal strings in GHS", () => {
    expect(formatMoney("1234.5")).toBe("GHS 1,234.50");
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("abc")).toBe("—");
  });
});

describe("finance role gating", () => {
  it.each(["teacher", "parent", "student"])("redirects a %s without calling the API", async (role) => {
    const authFetch = signInAs(role, () => json({}));
    render(<FinanceDashboardPage />);
    await waitFor(() => expect(auth.replace).toHaveBeenCalledWith("/"));
    expect(authFetch).not.toHaveBeenCalled();
    expect(screen.queryByText("Collected")).not.toBeInTheDocument();
  });
});

describe("FinanceDashboardPage", () => {
  it("shows collections separately from pending payments", async () => {
    signInAs("accountant", () => json(summary));
    render(<FinanceDashboardPage />);
    expect(await screen.findByText(/not counted as collected until confirmed/)).toBeInTheDocument();
    expect(screen.getAllByText("GHS 1,200.00").length).toBeGreaterThan(0);
  });

  it("shows the renewal notice when the subscription has lapsed", async () => {
    signInAs("owner", () => json({ detail: "Your subscription has expired." }, 402));
    render(<FinanceDashboardPage />);
    expect(await screen.findByRole("heading", { name: /subscription has expired/i })).toBeInTheDocument();
  });
});

describe("PaymentsPage", () => {
  const handler = (path: string, init?: RequestInit) => {
    if (init?.method === "POST") return json({ ...payment }, 201);
    if (path.includes("payment-methods")) return json(methods);
    if (path.includes("/payments/")) return json([payment]);
    return json([]);
  };

  it("hides reverse and refund from accountants", async () => {
    signInAs("accountant", (path, init) => (path.startsWith("/tenants/t1/students") ? json([]) : handler(path, init)));
    render(<PaymentsPage />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reverse" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
  });

  it("lets an owner reverse a confirmed payment only with a reason", async () => {
    const confirmed = { ...payment, status: "confirmed", receipt_number: "RCT-000001" };
    const authFetch = signInAs("owner", (path, init) => {
      if (init?.method === "POST") return json({ ...confirmed, status: "reversed" });
      if (path.includes("/payments/")) return json([confirmed]);
      if (path.includes("payment-methods")) return json(methods);
      return json([]);
    });
    const user = userEvent.setup();
    const prompt = vi.spyOn(window, "prompt");
    render(<PaymentsPage />);
    await screen.findByText("Esi Boateng");
    const posted = () => authFetch.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");

    prompt.mockReturnValueOnce("   ");
    await user.click(screen.getByRole("button", { name: "Reverse" }));
    expect(posted()).toHaveLength(0);

    prompt.mockReturnValueOnce("Wrong student");
    await user.click(screen.getByRole("button", { name: "Reverse" }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    const [path, init] = posted()[0] as [string, RequestInit];
    expect(path).toContain("/payments/p1/reverse/");
    expect(JSON.parse(String(init.body))).toEqual({ reason: "Wrong student" });
    prompt.mockRestore();
  });

  it("offers a refund only for money not applied to an invoice", async () => {
    const held = { ...payment, status: "confirmed", receipt_number: "RCT-000002", unallocated: "50.00" };
    const authFetch = signInAs("owner", (path, init) => {
      if (init?.method === "POST") return json(held);
      if (path.includes("/payments/")) return json([held]);
      if (path.includes("payment-methods")) return json(methods);
      return json([]);
    });
    const user = userEvent.setup();
    const prompt = vi.spyOn(window, "prompt");
    render(<PaymentsPage />);
    await screen.findByText("Esi Boateng");
    prompt.mockReturnValueOnce("50.00").mockReturnValueOnce("Parent withdrew child");
    await user.click(screen.getByRole("button", { name: "Refund" }));
    await waitFor(() =>
      expect(authFetch.mock.calls.some(([p]) => String(p).includes("/payments/p1/refund/"))).toBe(true),
    );
    const call = authFetch.mock.calls.find(([p]) => String(p).includes("/refund/")) as [string, RequestInit];
    expect(JSON.parse(String(call[1].body))).toEqual({ amount: "50.00", reason: "Parent withdrew child" });
    expect((call[1].headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    prompt.mockRestore();
  });

  it("explains that a mobile-money payment stays pending", async () => {
    const authFetch = signInAs("owner", (path, init) => (path.includes("/students") && !path.includes("finance") ? json([{ id: "s1", admission_number: "A-1", first_name: "Esi", last_name: "Boateng", grade_level: "", status: "active" }]) : handler(path, init)));
    const user = userEvent.setup();
    render(<PaymentsPage />);
    await screen.findByRole("option", { name: "Mobile money" });
    await user.selectOptions(screen.getByLabelText("Method"), "m2");
    expect(screen.getByText(/stay pending until you confirm/)).toBeInTheDocument();
    expect(authFetch).toHaveBeenCalled();
  });
});

describe("ReceiptPage", () => {
  it("renders the receipt and offers print", async () => {
    signInAs("accountant", () =>
      json({
        receipt_number: "RCT-000001",
        status: "confirmed",
        school: "North Star Academy",
        date: "2026-10-26",
        student: { name: "Esi Boateng", admission_number: "A-1", grade_level: "Class 3" },
        payer_name: "",
        method: "Cash",
        reference: "",
        amount: "500.00",
        allocations: [{ invoice: "INV-000001", amount: "500.00" }],
        advance_credit: "0.00",
      }),
    );
    render(<ReceiptPage />);
    expect(await screen.findByText("RCT-000001")).toBeInTheDocument();
    expect(screen.getByText("Invoice INV-000001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Print receipt" })).toBeInTheDocument();
  });
});

describe("ArrearsPage", () => {
  it("shows an empty state", async () => {
    signInAs("accountant", () => json([]));
    render(<ArrearsPage />);
    expect(await screen.findByText(/No outstanding balances/)).toBeInTheDocument();
  });
});

describe("AdvancesPage", () => {
  const row = {
    student: "s1", student_name: "Esi Boateng", admission_number: "A-1", grade_level: "Class 3",
    advance: "800.00", open_balance: "500.00", payments: [{ payment: "p1", receipt_number: "RCT-000001", advance: "800.00" }],
  };

  it("lists students holding an advance and applies it to their invoices", async () => {
    const authFetch = signInAs("accountant", (path, init) => (init?.method === "POST" ? json({}) : json([row])));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<AdvancesPage />);
    expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
    expect(screen.getAllByText("GHS 800.00").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Apply to invoices" }));
    await waitFor(() => {
      const call = authFetch.mock.calls.find(([path]) => String(path).includes("payments/p1/allocate/"));
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ auto_allocate: true });
    });
  });

  describe("FinanceSettingsPage", () => {
    it("shows configuration and creates an accounting period", async () => {
      const authFetch = signInAs("owner", (path, init) => {
        if (path.endsWith("/settings/")) return json({ currency: "GHS", invoice_prefix: "INV", receipt_prefix: "RCT", credit_note_prefix: "CN", default_payment_terms_days: 30 });
        if (path.endsWith("/accounts/")) return json([]);
        if (path.endsWith("/payment-methods/")) return json([]);
        if (path.endsWith("/periods/") && init?.method === "POST") return json({ id: "period1", name: "October 2026" }, 201);
        if (path.endsWith("/periods/")) return json([]);
        return json({});
      });
      const user = userEvent.setup();
      render(<FinanceSettingsPage />);
      expect(await screen.findByRole("heading", { name: "Finance settings" })).toBeInTheDocument();
      expect(await screen.findByRole("heading", { name: "Accounting periods" })).toBeInTheDocument();
      await user.type(screen.getByPlaceholderText("e.g. October 2026"), "October 2026");
      await user.type(screen.getByLabelText("Starts"), "2026-10-01");
      await user.type(screen.getByLabelText("Ends"), "2026-10-31");
      await user.click(screen.getByRole("button", { name: "Create period" }));
      await waitFor(() => {
        expect(authFetch).toHaveBeenCalledWith(
          expect.stringContaining("/periods/"),
          expect.objectContaining({ method: "POST" }),
        );
      });
      expect(await screen.findByText("Accounting period created.")).toBeInTheDocument();
    });
  });

  describe("FinanceReportsPage", () => {
    it("shows trial-balance totals and whether the ledger balances", async () => {
      signInAs("accountant", () => json({
        rows: [{ account: "1100 Cash", type: "asset", debit: "50.00", credit: "0.00" }],
        total_debit: "50.00",
        total_credit: "50.00",
        balanced: true,
      }));
      render(<FinanceReportsPage />);
      expect(await screen.findByText("Trial balance is balanced.")).toBeInTheDocument();
      expect(screen.getAllByText("GHS 50.00")).toHaveLength(3);
    });
  });

  describe("FinanceRemindersPage", () => {
    it("reviews overdue candidates without sending messages", async () => {
      const authFetch = signInAs("accountant", () => json([{
        student: "s1",
        student_name: "Esi Boateng",
        admission_number: "A-1",
        grade_level: "Class 3",
        invoices: 1,
        balance: "300.00",
        overdue_balance: "300.00",
        oldest_due_date: "2026-09-01",
        phones: ["0240000000"],
      }]));
      render(<FinanceRemindersPage />);
      expect(await screen.findByText("Esi Boateng")).toBeInTheDocument();
      expect(screen.getByText(/No SMS or WhatsApp provider is configured/)).toBeInTheDocument();
      expect(screen.getByText("1 on file")).toBeInTheDocument();
      expect(authFetch).toHaveBeenCalledTimes(1);
      expect(String(authFetch.mock.calls[0][0])).toContain("/reminders/");
    });
  });

  it("shows an empty state", async () => {
    signInAs("accountant", () => json([]));
    render(<AdvancesPage />);
    expect(await screen.findByText(/No student is holding an advance/)).toBeInTheDocument();
  });
});

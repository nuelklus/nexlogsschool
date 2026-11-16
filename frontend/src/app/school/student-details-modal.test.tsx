import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import StudentDetailsModal from "./student-details-modal";

const student = {
  id: "s1", admission_number: "A1", first_name: "Ama", last_name: "Mensah",
  date_of_birth: null, gender: "Female", grade_level: "Basic 1", current_class: "c1", campus: "campus1",
  status: "active" as const, enrolled_on: "2026-01-01", portal_account: false,
};
const schoolClass = { id: "c1", academic_year: "y1", name: "Basic 1", section: "A", room: "" };
const campus = { id: "campus1", name: "Main", code: "M", status: "active" };
const reply = (data: unknown, status = 200) => Response.json(data, { status });
const detail = {
  id: "i1", student: "s1", number: "INV-001", status: "issued", academic_year_name: "2026",
  period_label: "Term 1", issue_date: "2026-01-02", due_date: "2026-02-01", total: "1000.00", amount_paid: "200.00", credited_total: "0.00", balance: "800.00", brought_forward: "200.00", total_due: "1000.00",
  lines: [{ id: "l1", description: "Tuition", kind: "fee", amount: "1000.00" }],
};

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } });
});
afterEach(() => { cleanup(); Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); Reflect.deleteProperty(HTMLDialogElement.prototype, "close"); });

it("shows class, live account and latest non-void invoice without double counting arrears", async () => {
  const fetch = vi.fn(async (path: string) => {
    if (path.includes("statement/")) return reply({ outstanding_invoices: "1000.00", advance_credit: "50.00" });
    if (path.includes("?student=")) return reply([{ id: "void", student: "s1", status: "voided" }, { id: "i1", student: "s1", status: "issued" }]);
    return reply(detail);
  });
  render(<StudentDetailsModal student={student} schoolClass={schoolClass} campus={campus} authFetch={fetch} tenantId="school" onClose={vi.fn()} />);
  const dialog = screen.getByRole("dialog", { name: "Ama Mensah" });
  expect(within(dialog).getByText("Basic 1 A")).toBeInTheDocument();
  expect(within(dialog).getByText("Main")).toBeInTheDocument();
  const invoice = within(dialog).getByRole("region", { name: "Latest invoice" });
  expect(await within(invoice).findByText(/INV-001/)).toBeInTheDocument();
  expect(within(invoice).getByText(/Tuition/).closest("li")).toHaveTextContent("1,000.00");
  expect(within(invoice).getByText("Earlier invoices brought forward").nextElementSibling).toHaveTextContent("200.00");
  const account = within(dialog).getByRole("region", { name: "Student account" });
  expect(await within(account).findByText("Outstanding invoices")).toBeInTheDocument();
  expect(within(account).getByText("Unallocated advance credit").nextElementSibling).toHaveTextContent("50.00");
  expect(fetch).toHaveBeenCalledWith("/tenants/school/finance/invoices/?student=s1");
  expect(fetch).toHaveBeenCalledWith("/tenants/school/finance/invoices/i1/");
});

it("distinguishes a draft from an issued balance", async () => {
  const fetch = vi.fn(async (path: string) => path.includes("statement/") ? reply({ outstanding_invoices: "0.00", advance_credit: "0.00" })
    : path.includes("?student=") ? reply([{ id: "i1", student: "s1", status: "draft" }])
    : reply({ ...detail, number: "", status: "draft", balance: "0.00", brought_forward: "0.00", total_due: "0.00" }));
  render(<StudentDetailsModal student={student} schoolClass={schoolClass} campus={campus} authFetch={fetch} tenantId="school" onClose={vi.fn()} />);
  expect(await screen.findByText(/Draft amounts are not yet issued/)).toBeInTheDocument();
  expect(screen.queryByText("This invoice balance")).not.toBeInTheDocument();
});

it("shows financial errors separately while retaining profile and supports escape and close", async () => {
  const close = vi.fn();
  const fetch = vi.fn(async (path: string) => path.includes("statement/") ? reply({ detail: "Statement unavailable" }, 503)
    : reply({ detail: "Invoices unavailable" }, 503));
  render(<StudentDetailsModal student={student} schoolClass={null} campus={null} authFetch={fetch} tenantId="school" onClose={close} />);
  expect(await screen.findByText(/Statement unavailable/)).toBeInTheDocument();
  expect(await screen.findByText(/Invoices unavailable/)).toBeInTheDocument();
  expect(screen.getByText("Class details unavailable")).toBeInTheDocument();
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: false, cancelable: true }));
  expect(close).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(close).toHaveBeenCalledTimes(2);
});

it("shows empty invoices and rejects unexpected finance responses", async () => {
  const fetch = vi.fn(async (path: string) => path.includes("statement/") ? reply({ outstanding_invoices: "bad", advance_credit: "0.00" }) : reply([]));
  render(<StudentDetailsModal student={student} schoolClass={schoolClass} campus={campus} authFetch={fetch} tenantId="school" onClose={vi.fn()} />);
  expect(await screen.findByText(/No non-void invoices found among the 500/)).toBeInTheDocument();
  expect(await screen.findByText(/Unexpected account summary response/)).toBeInTheDocument();
});

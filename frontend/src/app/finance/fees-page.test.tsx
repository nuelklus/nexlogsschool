import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FeesPage from "./fees-page";

const auth = vi.hoisted(() => ({ authFetch: vi.fn() }));
vi.mock("../auth-provider", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "owner@example.com", full_name: "Owner", memberships: [{ tenant_id: "t1", tenant_name: "School", role: "owner" }] },
    isLoading: false,
    authFetch: auth.authFetch,
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
const years = [{ id: "y1", name: "2026/2027" }, { id: "y2", name: "2027/2028" }];
const categories = [{ id: "c1", code: "tuition", name: "Tuition", is_active: true }, { id: "c2", code: "bus", name: "Transport", is_active: true }];
const levels = [{ id: "l1", catalogue_level: null, catalogue_name: null, display_name: "Creche", sort_order: 1, is_active: true, is_terminal: false }];
const schedules = [
  { id: "f1", academic_year: "y1", category: "c1", category_name: "Tuition", grade_level: "", class_level: "l1", class_level_name: "Creche", amount: "300.00", effective_from: "2026-09-01", effective_to: null, status: "active", applicability: "compulsory", billing_basis: "fixed", unit_label: "", condition_type: "", condition_value: "" },
  { id: "f2", academic_year: "y2", category: "c2", category_name: "Transport", grade_level: "Basic 1", class_level: null, class_level_name: "", amount: "50.00", effective_from: "2027-09-01", effective_to: "2028-07-01", status: "inactive", applicability: "optional", billing_basis: "per_unit", unit_label: "trip", condition_type: "", condition_value: "" },
];

function defaultResponse(path: string) {
  if (path.endsWith("/fee-categories/")) return json(categories);
  if (path.endsWith("/academic-years/")) return json(years);
  if (path.endsWith("/class-levels/")) return json(levels);
  if (path.endsWith("/fee-schedules/")) return json(schedules);
  if (path.endsWith("/accounts/")) return json([{ id: "a1", code: "4000", name: "Tuition income", type: "income" }]);
  return json({}, 404);
}

beforeEach(() => {
  auth.authFetch.mockReset();
  auth.authFetch.mockImplementation((path: string) => defaultResponse(path));
});

async function fillFee() {
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "Creche" });
  await user.selectOptions(screen.getByLabelText("Academic year"), "y1");
  await user.selectOptions(screen.getByLabelText("Fee category"), "c1");
  await user.selectOptions(screen.getByLabelText("Class level"), "l1");
  await user.type(screen.getByLabelText("Amount (GHS)"), "100.00");
  fireEvent.change(screen.getByLabelText("Effective from"), { target: { value: "2026-09-01" } });
  return user;
}

describe("fee setup", () => {
  it("shows year, effective dates and filters schedules by search, year and status", async () => {
    const user = userEvent.setup();
    render(<FeesPage />);
    const table = await screen.findByRole("region", { name: "Fee schedules table" }, { timeout: 3000 });
    expect(within(table).getByText("2026/2027")).toBeInTheDocument();
    expect(within(table).getByText("to 2028-07-01")).toBeInTheDocument();
    expect(within(table).getByText("Basic 1")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Filter by academic year"), "y2");
    expect(within(table).queryByText("Tuition")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "active");
    expect(screen.getByText("No schedules match these filters.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "inactive");
    await user.type(screen.getByLabelText("Search fees"), "basic 1");
    expect(screen.getByText("Showing 1 of 2 schedules.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Activate Transport for Basic 1 in 2027\/2028/ })).toBeInTheDocument();
  });

  it("saves the existing compulsory fee payload", async () => {
    auth.authFetch.mockImplementation((path: string, init?: RequestInit) =>
      path.endsWith("/fee-schedules/") && init?.method === "POST" ? json({}, 201) : defaultResponse(path));
    render(<FeesPage />);
    const user = await fillFee();
    await user.click(screen.getByRole("button", { name: "Save fee schedule" }));
    await waitFor(() => expect(auth.authFetch).toHaveBeenCalledWith(
      "/tenants/t1/finance/fee-schedules/",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ academic_year: "y1", category: "c1", class_level: "l1", amount: "100.00", effective_from: "2026-09-01", effective_to: "", applicability: "compulsory", billing_basis: "fixed", unit_label: "", condition_type: "", condition_value: "" }) }),
    ));
    expect(await screen.findByText(/Fee schedule saved/)).toBeInTheDocument();
  });

  it("saves conditional per-unit rules and displays server errors", async () => {
    auth.authFetch.mockImplementation((path: string, init?: RequestInit) =>
      path.endsWith("/fee-schedules/") && init?.method === "POST" ? json({ detail: "Unable to save fee." }, 400) : defaultResponse(path));
    render(<FeesPage />);
    const user = await fillFee();
    await user.selectOptions(screen.getByLabelText("Who is charged"), "conditional");
    await user.selectOptions(screen.getByLabelText("Billed"), "per_unit");
    await user.type(screen.getByLabelText("Unit name"), "meal");
    await user.selectOptions(screen.getByLabelText("Condition"), "service");
    await user.selectOptions(screen.getByLabelText("Service"), "bus");
    await user.click(screen.getByRole("button", { name: "Save fee schedule" }));
    await waitFor(() => expect(auth.authFetch).toHaveBeenCalledWith(
      "/tenants/t1/finance/fee-schedules/",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"billing_basis":"per_unit","unit_label":"meal","condition_type":"service","condition_value":"bus"') }),
    ));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to save fee.");
  });

  it("toggles a schedule with contextual feedback", async () => {
    auth.authFetch.mockImplementation((path: string, init?: RequestInit) =>
      init?.method === "PATCH" ? json({}) : defaultResponse(path));
    render(<FeesPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Deactivate Tuition for Creche/ }));
    await waitFor(() => expect(auth.authFetch).toHaveBeenCalledWith(
      "/tenants/t1/finance/fee-schedules/f1/",
      expect.objectContaining({ method: "PATCH", body: '{"status":"inactive"}' }),
    ));
    expect(await screen.findByText("Tuition fee schedule deactivated.")).toBeInTheDocument();
  });

  it("reports a failed status change without claiming success", async () => {
    auth.authFetch.mockImplementation((path: string, init?: RequestInit) =>
      init?.method === "PATCH" ? json({ detail: "Status change failed." }, 400) : defaultResponse(path));
    render(<FeesPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Deactivate Tuition for Creche/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Status change failed.");
    expect(screen.queryByText("Tuition fee schedule deactivated.")).not.toBeInTheDocument();
  });

  it("adds a category with its income account", async () => {
    auth.authFetch.mockImplementation((path: string, init?: RequestInit) =>
      path.endsWith("/fee-categories/") && init?.method === "POST" ? json({}, 201) : defaultResponse(path));
    render(<FeesPage />);
    const form = await screen.findByRole("form", { name: "Add fee category" });
    const user = userEvent.setup();
    await user.type(within(form).getByLabelText("Name"), "Lunch");
    await user.type(within(form).getByLabelText("Code"), "lunch");
    await within(form).findByRole("option", { name: "4000 Tuition income" });
    await user.selectOptions(within(form).getByLabelText("Income account"), "a1");
    await user.click(within(form).getByRole("button", { name: "Add category" }));
    await waitFor(() => expect(auth.authFetch).toHaveBeenCalledWith(
      "/tenants/t1/finance/fee-categories/",
      expect.objectContaining({ method: "POST", body: '{"code":"lunch","name":"Lunch","income_account":"a1"}' }),
    ));
    expect(await screen.findByText("Fee category added.")).toBeInTheDocument();
  });

  it("blocks saving after class levels fail and allows a retry", async () => {
    let attempts = 0;
    auth.authFetch.mockImplementation((path: string) => {
      if (path.endsWith("/class-levels/") && attempts++ === 0) return json({ detail: "Class levels unavailable." }, 500);
      return defaultResponse(path);
    });
    render(<FeesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Class levels unavailable.");
    expect(screen.getByRole("button", { name: "Save fee schedule" })).toBeDisabled();
    expect(screen.getByLabelText("Class level")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry class levels" }));
    await screen.findByRole("option", { name: "Creche" });
    expect(screen.getByRole("button", { name: "Save fee schedule" })).toBeEnabled();
  });

  it("shows academic year load failures instead of an empty year selector", async () => {
    auth.authFetch.mockImplementation((path: string) =>
      path.endsWith("/academic-years/") ? json({ detail: "Years unavailable." }, 500) : defaultResponse(path));
    render(<FeesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Years unavailable.");
    expect(screen.queryByRole("form", { name: "Add fee schedule" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry academic years" })).toBeInTheDocument();
  });
});

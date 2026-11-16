import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassLevelAssignment from "./class-level-assignment";

const year = { id: "y", name: "2026/27", start_date: "2026-01-01", end_date: "2027-01-01", status: "active", is_current: true };
const schoolClass = { id: "c", academic_year: "y", name: "Grade 1", section: "A", room: "", class_level: null };
const levels = [
  { id: "l", catalogue_level: "gh-basic-1", catalogue_name: "Basic 1", display_name: "Basic 1", sort_order: 1, is_active: true, is_terminal: false },
  { id: "old", catalogue_level: null, catalogue_name: null, display_name: "Old", sort_order: 2, is_active: false, is_terminal: false },
];
const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

describe("ClassLevelAssignment", () => {
  it("explicitly assigns a level without changing the existing class", async () => {
    const fetch = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH" ? reply({ ...schoolClass, class_level: "l" }) : reply(levels));
    const onUpdated = vi.fn();
    render(<ClassLevelAssignment authFetch={fetch} tenantId="t" classes={[schoolClass]} years={[year]} onUpdated={onUpdated} />);
    const select = await screen.findByLabelText("Grade 1 · A · 2026/27");
    expect(screen.queryByRole("option", { name: /Old/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "l");
    await userEvent.click(screen.getByRole("button", { name: "Save level for Grade 1 · A · 2026/27" }));
    await screen.findByText("Assignment saved.");
    const patch = fetch.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patch?.[0]).toBe("/tenants/t/academics/classes/c/level/");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ class_level: "l" });
    expect(onUpdated).toHaveBeenCalledWith({ ...schoolClass, class_level: "l" });
    expect(screen.getByRole("button", { name: /Save level for/ })).toBeDisabled();
  });

  it("displays an existing archived link and sends an explicit clear", async () => {
    const fetch = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH" ? reply(schoolClass) : reply(levels));
    render(<ClassLevelAssignment authFetch={fetch} tenantId="t" classes={[{ ...schoolClass, class_level: "old" }]} years={[year]} onUpdated={vi.fn()} />);
    expect(await screen.findByRole("option", { name: "Old (archived)" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Grade 1 · A · 2026/27"), "");
    await userEvent.click(screen.getByRole("button", { name: /Save level for/ }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
    expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "PATCH")?.[1]?.body))).toEqual({ class_level: null });
  });

  it("surfaces loading errors and provides retry", async () => {
    const fetch = vi.fn(async () => reply({ detail: "Not available" }, 500));
    render(<ClassLevelAssignment authFetch={fetch} tenantId="t" classes={[schoolClass]} years={[year]} onUpdated={vi.fn()} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
});

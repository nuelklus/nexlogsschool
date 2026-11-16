import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassManagement from "./class-management";

const year = { id: "y", name: "2026/27", start_date: "2026-01-01", end_date: "2027-01-01", status: "active", is_current: true };
const schoolClass = { id: "c", academic_year: "y", name: "Basic 1", section: "A", room: "", class_level: null, class_teacher: null, is_active: true };
const teachers = [{ id: "teacher", name: "Teacher One", email: "teacher@example.com" }];

describe("ClassManagement", () => {
  it("saves the chosen teacher and archive status", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ ...schoolClass, class_teacher: "teacher", is_active: false })));
    const updated = vi.fn();
    render(<ClassManagement authFetch={fetch} tenantId="t" classes={[schoolClass]} years={[year]} teachers={teachers} onUpdated={updated} />);
    await userEvent.selectOptions(screen.getByLabelText("Class teacher for Basic 1 · A · 2026/27"), "teacher");
    await userEvent.click(screen.getByLabelText("Active class"));
    await userEvent.click(screen.getByRole("button", { name: /Save settings/ }));
    expect(await screen.findByText("Class settings saved.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/t/academics/classes/c/management/", expect.objectContaining({
      method: "PATCH", body: JSON.stringify({ class_teacher: "teacher", is_active: false }),
    }));
    expect(updated).toHaveBeenCalledWith(expect.objectContaining({ is_active: false }));
  });

  it("reports errors without hiding the existing class", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "Not available" }), { status: 400 }));
    render(<ClassManagement authFetch={fetch} tenantId="t" classes={[schoolClass]} years={[year]} teachers={teachers} onUpdated={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Save settings/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not available");
    expect(screen.getByText("Basic 1 · A · 2026/27")).toBeInTheDocument();
  });
});

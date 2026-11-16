import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import CreateClassForm from "./create-class-form";

const years = [{ id: "y", name: "2026/27", start_date: "2026-01-01", end_date: "2027-01-01", status: "active", is_current: true }];
const levels = [
  { id: "l1", catalogue_level: "gh-creche", catalogue_name: "Creche", display_name: "Creche", sort_order: 1, is_active: true, is_terminal: false },
  { id: "l2", catalogue_level: null, catalogue_name: null, display_name: "Reception", sort_order: 2, is_active: true, is_terminal: false },
  { id: "old", catalogue_level: null, catalogue_name: null, display_name: "Archived", sort_order: 3, is_active: false, is_terminal: false },
];
const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const campuses = [{ id: "m", name: "Main", code: "MAIN", status: "active" }];

describe("CreateClassForm", () => {
  it("suggests the selected level name, permits custom names, and submits its level", async () => {
    const fetch = vi.fn(async () => reply(levels));
    const create = vi.fn(async () => {});
    const user = userEvent.setup();
    render(<CreateClassForm authFetch={fetch} tenantId="school" years={years} campuses={campuses} onCreate={create} />);
    await screen.findByRole("option", { name: "Creche" });
    expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/class-levels/");
    expect(screen.queryByRole("option", { name: "Archived" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Academic year"), "y");
    await user.selectOptions(screen.getByLabelText("Class level"), "l1");
    expect(screen.getByLabelText("Class name")).toHaveValue("Creche");
    await user.selectOptions(screen.getByLabelText("Class level"), "l2");
    expect(screen.getByLabelText("Class name")).toHaveValue("Reception");
    await user.clear(screen.getByLabelText("Class name"));
    await user.type(screen.getByLabelText("Class name"), "Reception Blue");
    await user.selectOptions(screen.getByLabelText("Class level"), "l1");
    expect(screen.getByLabelText("Class name")).toHaveValue("Reception Blue");
    await user.type(screen.getByLabelText("Section"), "A");
    await user.click(screen.getByRole("button", { name: "Create class" }));
    await screen.findByText("Class created successfully.");
    expect(create).toHaveBeenCalledWith({ academic_year: "y", class_level: "l1", name: "Reception Blue", section: "A", room: "", campus: "m" });
    expect(screen.getByLabelText("Class name")).toHaveValue("");
  });

  it("allows explicit unassigned class creation for schools without levels", async () => {
    const create = vi.fn(async () => {});
    const user = userEvent.setup();
    render(<CreateClassForm authFetch={async () => reply([])} tenantId="school" years={years} campuses={campuses} onCreate={create} />);
    await screen.findByText(/No active levels are configured/);
    await user.selectOptions(screen.getByLabelText("Academic year"), "y");
    await user.selectOptions(screen.getByLabelText("Class level"), "unassigned");
    await user.type(screen.getByLabelText("Class name"), "Legacy class");
    await user.click(screen.getByRole("button", { name: "Create class" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ academic_year: "y", name: "Legacy class", section: "", room: "", campus: "m" }));
  });

  it("reports loading and save failures and retries loading", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply({ detail: "Not available" }, 500)).mockResolvedValue(reply(levels));
    const create = vi.fn().mockRejectedValue(new Error("Class already exists."));
    const user = userEvent.setup();
    render(<CreateClassForm authFetch={fetch} tenantId="school" years={years} campuses={campuses} onCreate={create} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not available");
    expect(screen.getByRole("button", { name: "Create class" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("option", { name: "Creche" });
    await user.selectOptions(screen.getByLabelText("Academic year"), "y");
    await user.selectOptions(screen.getByLabelText("Class level"), "l1");
    await user.click(screen.getByRole("button", { name: "Create class" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Class already exists.");
    expect(screen.getByLabelText("Class name")).toHaveValue("Creche");
  });

  it("requires an active campus and asks for a choice when there are several", async () => {
    const create = vi.fn(async () => {});
    const { rerender } = render(<CreateClassForm authFetch={async () => reply(levels)} tenantId="school" years={years} onCreate={create} />);
    await screen.findByRole("option", { name: "Creche" });
    expect(screen.getByText(/Create an active campus/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create class" })).toBeDisabled();
    rerender(<CreateClassForm authFetch={async () => reply(levels)} tenantId="school" years={years} campuses={[...campuses, { id: "s", name: "South", code: "S", status: "active" }]} onCreate={create} />);
    expect(screen.getByLabelText("Campus")).toHaveValue("");
    expect(screen.getByLabelText("Campus")).toBeRequired();
  });
});

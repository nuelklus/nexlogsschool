import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassSetupPanel from "./class-setup-panel";

const year = { id: "y", name: "2026/27", start_date: "2026-01-01", end_date: "2027-01-01", status: "active", is_current: true };
const level = { id: "l", catalogue_level: "gh-creche", catalogue_name: "Creche", display_name: "Creche", sort_order: 1, is_active: true, is_terminal: false };
const campus = { id: "m", code: "MAIN", name: "Main", status: "active" };
const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

describe("school class setup", () => {
  it("creates a level-linked class from Overview and refreshes the list", async () => {
    let classes: object[] = [];
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path.endsWith("class-level-catalogue/")) return reply([]);
      if (path.endsWith("class-levels/")) return reply([level]);
      if (path.endsWith("years/")) return reply([year]);
      if (path.endsWith("teachers/")) return reply([]);
      if (path.endsWith("finance/campuses/")) return reply([campus]);
      if (path.endsWith("classes/") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        const created = { id: "c", ...body };
        classes = [created];
        return reply(created, 201);
      }
      if (path.endsWith("classes/")) return reply(classes);
      return reply({ detail: "Unknown route" }, 404);
    });
    const user = userEvent.setup();
    render(<ClassSetupPanel tenantId="school" authFetch={fetch} />);
    expect(await screen.findByRole("heading", { name: "Creche" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Classes", pressed: false }));
    await screen.findByRole("option", { name: "Creche" });
    await user.selectOptions(screen.getByLabelText("Academic year"), "y");
    await user.selectOptions(screen.getByLabelText("Class level"), "l");
    await user.selectOptions(screen.getByLabelText("Campus"), "m");
    await user.type(screen.getByLabelText("Section"), "A");
    await user.click(screen.getByRole("button", { name: "Create class" }));
    expect(await screen.findByText("Class created successfully.")).toBeInTheDocument();
    expect(screen.getByText("Creche · A", { selector: "strong" })).toBeInTheDocument();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/tenants/school/academics/classes/");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ academic_year: "y", class_level: "l", name: "Creche", section: "A", room: "", campus: "m" });
    expect(within(screen.getByRole("region", { name: "School class setup" })).getByRole("region", { name: "Legacy class reconciliation" })).toBeInTheDocument();
  });

  it("reports class loading failures and retries", async () => {
    let fail = true;
    const fetch = vi.fn(async (path: string) => {
      if (path.endsWith("class-levels/") || path.endsWith("class-level-catalogue/")) return reply([]);
      if (fail && path.endsWith("classes/")) return reply({ detail: "Cannot load classes." }, 500);
      if (path.endsWith("years/")) return reply([year]);
      return reply([]);
    });
    const user = userEvent.setup();
    render(<ClassSetupPanel tenantId="school" authFetch={fetch} />);
    await user.click(screen.getByRole("button", { name: "Classes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot load classes.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect((await screen.findAllByText("No classes have been added.")).length).toBeGreaterThan(0);
  });

  it("does not display levels from a previous school after switching", async () => {
    let resolveOld!: (response: Response) => void;
    const fetch = vi.fn((path: string) => {
      if (path.includes("/old/") && path.endsWith("class-levels/")) return new Promise<Response>((resolve) => { resolveOld = resolve; });
      if (path.endsWith("class-level-catalogue/")) return Promise.resolve(reply([]));
      return Promise.resolve(reply([{ ...level, id: "new", display_name: "New school level" }]));
    });
    const { rerender } = render(<ClassSetupPanel key="old" tenantId="old" authFetch={fetch} />);
    rerender(<ClassSetupPanel key="new" tenantId="new" authFetch={fetch} />);
    expect(await screen.findByRole("heading", { name: "New school level" })).toBeInTheDocument();
    await act(async () => resolveOld(reply([level])));
    expect(screen.queryByRole("heading", { name: "Creche" })).not.toBeInTheDocument();
  });
});

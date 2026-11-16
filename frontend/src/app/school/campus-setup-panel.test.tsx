import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import CampusSetupPanel from "./campus-setup-panel";

const campus = { id: "c1", name: "Main", code: "M", address: "", contact_phone: "", contact_email: "", status: "active" };

describe("Campus setup", () => {
  it("creates and updates campuses for the selected school", async () => {
    let rows: object[] = [];
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") { rows = [campus]; return Response.json(campus, { status: 201 }); }
      if (init?.method === "PATCH") { rows = [{ ...campus, status: "inactive" }]; return Response.json(rows[0]); }
      return Response.json(rows);
    });
    const changed = vi.fn();
    render(<CampusSetupPanel tenantId="school" authFetch={fetch} onChanged={changed} />);
    const user = userEvent.setup();
    await screen.findByText("No campuses yet. Create your first campus.");
    await user.type(screen.getByLabelText("Campus name"), "Main");
    await user.type(screen.getByLabelText("Campus code"), "M");
    await user.click(screen.getByRole("button", { name: "Create campus" }));
    expect(await screen.findByText("Campus created.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/school/campuses/", expect.objectContaining({ method: "POST" }));
    await user.click(screen.getByRole("button", { name: "Edit Main" }));
    await user.selectOptions(screen.getByLabelText("Status"), "inactive");
    await user.click(screen.getByRole("button", { name: "Save campus changes" }));
    expect(await screen.findByText("Campus updated.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "School campuses" })).getByText("inactive")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/school/campuses/c1/", expect.objectContaining({ method: "PATCH" }));
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it("shows a clear error if saved campus list cannot refresh", async () => {
    let saved = false;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") { saved = true; return Response.json(campus, { status: 201 }); }
      return saved ? Response.json({ detail: "Offline" }, { status: 500 }) : Response.json([]);
    });
    render(<CampusSetupPanel tenantId="school" authFetch={fetch} onChanged={vi.fn()} />);
    const user = userEvent.setup();
    await screen.findByText("No campuses yet. Create your first campus.");
    await user.type(screen.getByLabelText("Campus name"), "Main");
    await user.type(screen.getByLabelText("Campus code"), "M");
    await user.click(screen.getByRole("button", { name: "Create campus" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The campus was saved");
    expect(screen.queryByRole("button", { name: "Create campus" })).not.toBeInTheDocument();
  });
});

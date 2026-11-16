import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import CampusAccessPanel from "./campus-access-panel";
import type { FinanceContext } from "./finance-shell";

describe("CampusAccessPanel", () => {
  it("saves explicit campus grants", async () => {
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify({ membership: "m", campuses: ["c"] }));
      if (path.endsWith("campuses/")) return new Response(JSON.stringify([{ id: "c", code: "MAIN", name: "Main", status: "active" }]));
      return new Response(JSON.stringify([{ membership: "m", name: "Accountant", email: "a@example.com", status: "active", campuses: [] }]));
    });
    const ctx: FinanceContext = { authFetch: fetch, tenantId: "t", userId: "u", canApprove: true, onFailure: vi.fn() };
    render(<CampusAccessPanel ctx={ctx} />);
    await userEvent.click(await screen.findByLabelText("Main (MAIN)"));
    await userEvent.click(screen.getByRole("button", { name: "Save campus access for Accountant" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/tenants/t/finance/campus-access/m/",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ campuses: ["c"] }) }),
    ));
    expect(await screen.findByText("Campus access saved.")).toBeInTheDocument();
  });
});

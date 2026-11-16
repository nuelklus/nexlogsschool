import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ClassLevelsPanel from "./class-levels-panel";

const catalogue = [{ code: "gh-basic-1", name: "Basic 1", sort_order: 6 }];
const level = {
  id: "level-1", catalogue_level: "gh-basic-1", catalogue_name: "Basic 1",
  display_name: "Grade 1", sort_order: 6, is_active: true, is_terminal: false,
};

function response(data: unknown, ok = true) {
  return new Response(JSON.stringify(data), {
    status: ok ? 200 : 500,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ClassLevelsPanel", () => {
  it("adds a catalogue level with a school-specific name", async () => {
    let configured = false;
    const authFetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        configured = true;
        return response(level);
      }
      return response(path.includes("class-level-catalogue") ? catalogue : configured ? [level] : []);
    });
    render(<ClassLevelsPanel authFetch={authFetch} tenantId="t1" canManage />);
    await userEvent.selectOptions(await screen.findByLabelText("Ghana catalogue level"), "gh-basic-1");
    await userEvent.type(screen.getByLabelText("Display name (required for custom levels)"), "Grade 1");
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await screen.findByRole("button", { name: "Save Grade 1" });
    const post = authFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      catalogue_level: "gh-basic-1", display_name: "Grade 1", sort_order: 6,
    });
  });

  it("archives a configured level and leaves reads available to non-managers", async () => {
    const authFetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") return response({ ...level, is_active: false });
      return response(path.includes("class-level-catalogue") ? catalogue : [level]);
    });
    const { unmount } = render(<ClassLevelsPanel authFetch={authFetch} tenantId="t1" canManage />);
    const active = await screen.findByLabelText("Active");
    await userEvent.click(active);
    await userEvent.click(screen.getByRole("button", { name: "Save Grade 1" }));
    await waitFor(() => expect(authFetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
    const patch = authFetch.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body)).is_active).toBe(false);
    unmount();
    render(<ClassLevelsPanel authFetch={authFetch} tenantId="t1" canManage={false} />);
    expect(await screen.findByText("Active")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add level" })).not.toBeInTheDocument();
  });

  it("reports load errors instead of showing an empty configuration", async () => {
    const authFetch = vi.fn(async () => response({ detail: "Not available" }, false));
    render(<ClassLevelsPanel authFetch={authFetch} tenantId="t1" canManage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No class levels configured yet.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(4));
  });
});

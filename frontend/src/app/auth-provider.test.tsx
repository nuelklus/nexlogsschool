import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./auth-provider";

const user = {
  id: "u1",
  email: "u@example.com",
  full_name: "Test User",
  memberships: [{ tenant_id: "t1", tenant_name: "North Star", role: "owner" }],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function Probe({ onReady }: { onReady: (fetcher: ReturnType<typeof useAuth>["authFetch"]) => void }) {
  const { user: current, authFetch } = useAuth();
  if (current) onReady(authFetch);
  return <p>{current ? "signed in" : "signed out"}</p>;
}

describe("authFetch", () => {
  it("refreshes the session once and retries after a 401", async () => {
    let refreshes = 0;
    const seenTokens: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = String(url).replace(/^.*\/api/, "");
        if (path === "/auth/refresh/") {
          refreshes += 1;
          return json({ access: `token-${refreshes}` });
        }
        if (path === "/auth/me/") return json(user);
        seenTokens.push(new Headers(init?.headers).get("Authorization"));
        return seenTokens.length === 1 ? json({ detail: "expired" }, 401) : json({ ok: true });
      }),
    );

    let fetcher: ReturnType<typeof useAuth>["authFetch"] | undefined;
    render(
      <AuthProvider>
        <Probe onReady={(f) => (fetcher = f)} />
      </AuthProvider>,
    );
    await screen.findByText("signed in");

    const response = await fetcher!("/tenants/t1/students/");
    expect(response.status).toBe(200);
    expect(refreshes).toBe(2);
    expect(seenTokens).toEqual(["Bearer token-1", "Bearer token-2"]);
    vi.unstubAllGlobals();
  });

  it("signs the user out when the refresh is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = String(url).replace(/^.*\/api/, "");
        if (path === "/auth/refresh/") {
          return vi.mocked(fetch).mock.calls.length <= 1
            ? json({ access: "token-1" })
            : json({ detail: "bad" }, 401);
        }
        if (path === "/auth/me/") return json(user);
        return json({ detail: "expired" }, 401);
      }),
    );

    let fetcher: ReturnType<typeof useAuth>["authFetch"] | undefined;
    render(
      <AuthProvider>
        <Probe onReady={(f) => (fetcher = f)} />
      </AuthProvider>,
    );
    await screen.findByText("signed in");

    const response = await fetcher!("/tenants/t1/students/");
    expect(response.status).toBe(401);
    await waitFor(() => expect(screen.getByText("signed out")).toBeInTheDocument());
    vi.unstubAllGlobals();
  });
});

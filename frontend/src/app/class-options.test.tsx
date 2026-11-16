import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CampusOptions, ClassOptions, ClassOptionsStatus, useClassOptions } from "./class-options";
import type { AuthFetch } from "./school/student-api";

function Selector({ authFetch, tenant }: { authFetch: AuthFetch; tenant: string }) {
  const classes = useClassOptions(authFetch, tenant, "finance");
  return <>
    <select aria-label="Class" disabled={!classes.options}><ClassOptions options={classes.options} /></select>
    <ClassOptionsStatus options={classes.options} error={classes.error} />
  </>;
}

describe("shared options", () => {
  it("uses backend values and reports malformed responses", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(["Grade 1", "Custom class"])));
    const { rerender } = render(<Selector authFetch={fetch} tenant="one" />);
    expect(await screen.findByRole("option", { name: "Custom class" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Grade 1" })).toBeInTheDocument();
    const badFetch = vi.fn(async () => new Response(JSON.stringify({ invalid: true })));
    rerender(<Selector authFetch={badFetch} tenant="one" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected class options");
    expect(screen.getByRole("combobox")).toBeDisabled();
  });

  it("does not display an old tenant's late response", async () => {
    let resolveOld!: (response: Response) => void;
    const fetch: AuthFetch = (path) => path.includes("/one/")
      ? new Promise((resolve) => { resolveOld = resolve; })
      : Promise.resolve(new Response(JSON.stringify(["New class"])));
    const { rerender } = render(<Selector authFetch={fetch} tenant="one" />);
    rerender(<Selector authFetch={fetch} tenant="two" />);
    expect(await screen.findByRole("option", { name: "New class" })).toBeInTheDocument();
    await act(async () => resolveOld(new Response(JSON.stringify(["Old class"]))));
    expect(screen.queryByRole("option", { name: "Old class" })).not.toBeInTheDocument();
  });

  it("shows only active campuses from the provided scoped list", () => {
    render(<select aria-label="Campus"><CampusOptions campuses={[
      { id: "1", name: "Main", code: "M", status: "active" },
      { id: "2", name: "Closed", code: "C", status: "inactive" },
    ]} /></select>);
    expect(screen.getByRole("option", { name: "M Main" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "C Closed" })).not.toBeInTheDocument();
  });
});

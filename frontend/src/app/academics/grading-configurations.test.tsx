import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import GradingConfigurations from "./grading-configurations";

const first = {
  id: "first", family: "family", version: 1, name: "Term 1 grading", class_level: "level",
  examination_type: "End of Term",
  configuration: {
    components: [{ key: "Class Test", maximum: "40.00", weight: "40.00" }, { key: "Exam", maximum: "60.00", weight: "60.00" }],
    bands: [{ minimum: "0.00", grade: "F", description: "Needs work", passed: false }],
    decimal_places: 2, rounding: "half_up" as const, allow_decimal_scores: true, exclude_not_applicable: false,
  },
};
const second = { ...first, id: "second", version: 2 };

describe("Grading configurations", () => {
  it("displays saved details and permits editing only the latest version", async () => {
    const fetch = vi.fn<(path: string, init?: RequestInit) => Promise<Response>>(async () => Response.json(second));
    const refresh = vi.fn(async () => {});
    render(<GradingConfigurations authFetch={fetch} tenantId="school" canManage configs={[first, second]} onSaved={refresh} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Term 1 grading · End of Term · v1 · Previous version"));
    expect(screen.getAllByText("Class Test: maximum 40.00, weight 40.00%")).toHaveLength(2);
    expect(screen.getAllByText("From 0.00%: F · Needs work · Not passing")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Edit Term 1 grading v1" })).not.toBeInTheDocument();
    await user.click(screen.getByText("Term 1 grading · End of Term · v2 · Latest"));
    await user.click(screen.getByRole("button", { name: "Edit Term 1 grading v2" }));
    const form = within(screen.getByRole("region", { name: "Edit grading configuration" }));
    expect(form.getByLabelText("Examination type")).toHaveValue("End of Term");
    const component = within(form.getByRole("group", { name: "Component 1" }));
    expect(component.getByLabelText("Maximum")).toHaveValue(40);
    await user.clear(component.getByLabelText("Name"));
    await user.type(component.getByLabelText("Name"), "Coursework");
    await user.click(form.getByRole("button", { name: "Save new version" }));
    expect(await screen.findByText("New grading configuration version saved.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/grading-configurations/second/revisions/", expect.objectContaining({ method: "POST" }));
    const body = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(body.class_level).toBe("level");
    expect(body.configuration.components).toEqual([
      { key: "Coursework", maximum: "40.00", weight: "40.00" },
      { key: "Exam", maximum: "60.00", weight: "60.00" },
    ]);
    expect(body.configuration.allow_decimal_scores).toBe(true);
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("creates a configuration and refreshes the list", async () => {
    const fetch = vi.fn(async () => Response.json(first));
    const refresh = vi.fn(async () => {});
    render(<GradingConfigurations authFetch={fetch} tenantId="school" canManage configs={[]} onSaved={refresh} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Create grading configuration"));
    await user.type(screen.getByLabelText("Name", { selector: 'input[name="name"]' }), "Term 1 grading");
    await user.type(screen.getByLabelText("Examination type"), "End of Term");
    const component = within(screen.getByRole("group", { name: "Component 1" }));
    await user.type(component.getByLabelText("Name"), "Class Test");
    await user.type(component.getByLabelText("Maximum"), "40");
    await user.type(component.getByLabelText("Weight (%)"), "100");
    await user.type(screen.getByLabelText("Grade"), "F");
    await user.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByText("Grading configuration saved.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/tenants/school/academics/grading-configurations/", expect.objectContaining({ method: "POST" }));
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Examination type")).toHaveValue("");
  });

  it("preserves edits and shows backend errors when a revision is stale", async () => {
    const fetch = vi.fn(async () => Response.json({ detail: "This version is no longer current. Reload before creating a revision." }, { status: 400 }));
    const refresh = vi.fn(async () => {});
    render(<GradingConfigurations authFetch={fetch} tenantId="school" canManage configs={[first]} onSaved={refresh} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Term 1 grading · End of Term · v1 · Latest"));
    await user.click(screen.getByRole("button", { name: "Edit Term 1 grading v1" }));
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This version is no longer current");
    expect(screen.getByRole("region", { name: "Edit grading configuration" })).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("reports a refresh failure without inviting a duplicate save", async () => {
    const fetch = vi.fn(async () => Response.json(second));
    render(<GradingConfigurations authFetch={fetch} tenantId="school" canManage configs={[first]} onSaved={async () => { throw new Error("offline"); }} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Term 1 grading · End of Term · v1 · Latest"));
    await user.click(screen.getByRole("button", { name: "Edit Term 1 grading v1" }));
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("configuration was saved");
    expect(screen.queryByRole("region", { name: "Edit grading configuration" })).not.toBeInTheDocument();
  });

  it("allows teachers to inspect but not create or revise configurations", async () => {
    render(<GradingConfigurations authFetch={vi.fn()} tenantId="school" canManage={false} configs={[first]} onSaved={vi.fn()} />);
    await userEvent.click(screen.getByText("Term 1 grading · End of Term · v1 · Latest"));
    expect(screen.getByText("Exam: maximum 60.00, weight 60.00%")).toBeInTheDocument();
    expect(screen.queryByText("Create grading configuration")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit Term/ })).not.toBeInTheDocument();
  });
});

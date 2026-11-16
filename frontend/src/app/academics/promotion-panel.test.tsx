import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import PromotionPanel from "./promotion-panel";

const years = [
  { id: "year1", name: "2026", start_date: "2026-01-01", end_date: "2026-12-31", status: "active", is_current: true },
  { id: "year2", name: "2027", start_date: "2027-01-01", end_date: "2027-12-31", status: "active", is_current: false },
];
const classes = [
  { id: "class1", academic_year: "year1", name: "Basic 1", section: "A", room: "" },
  { id: "class2", academic_year: "year1", name: "Basic 2", section: "A", room: "", is_active: false },
  { id: "class3", academic_year: "year2", name: "Basic 2", section: "A", room: "" },
];
const row = (id: string, name: string) => ({
  enrollment: id, student_name: name, admission_number: id, from_class: "Basic 1 A",
  suggested_action: "promote", suggested_class: "class3", already_enrolled: false,
});

describe("Promotion by class", () => {
  it("loads and applies only the selected class with school-wide year change opt-in", async () => {
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return Response.json({ promote: 0, repeat: 2, graduate: 0, leave: 0 });
      expect(new URL(path, "http://localhost").searchParams.get("source_class")).toBe("class1");
      return Response.json({ target_classes: [{ id: "class3", name: "Basic 2 A" }], rows: [row("one", "Ama"), row("two", "Kofi")] });
    });
    const refreshed = vi.fn(async () => {});
    render(<PromotionPanel authFetch={fetch} tenantId="school" years={years} classes={classes} onApplied={refreshed} />);
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Load students" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Basic 2 A (archived)" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Source class"), "class1");
    await user.click(screen.getByRole("button", { name: "Load students" }));
    expect(await screen.findByText("Showing students in Basic 1 A. Actions below apply only to this class.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Make the new year/ })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Repeat class" }));
    expect(screen.getByLabelText("Action for Ama")).toHaveValue("repeat");
    expect(screen.getByLabelText("Action for Kofi")).toHaveValue("repeat");
    expect(screen.getByLabelText("Next class for Ama")).toHaveValue("");
    expect(screen.getByLabelText("Next class for Kofi")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Apply to 2 students" }));
    expect(await screen.findByRole("status")).toHaveTextContent("2 repeating");
    const request = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      from_year: "year1", to_year: "year2", make_current: false,
      decisions: [{ enrollment: "one", action: "repeat", target_class: "" }, { enrollment: "two", action: "repeat", target_class: "" }],
    });
    expect(refreshed).toHaveBeenCalledOnce();
  });

  it("clears the previous preview on class or year changes and shows empty classes", async () => {
    const fetch = vi.fn(async (path: string) => {
      const selected = new URL(path, "http://localhost").searchParams.get("source_class");
      return Response.json({ target_classes: [], rows: selected === "class1" ? [row("one", "Ama")] : [] });
    });
    render(<PromotionPanel authFetch={fetch} tenantId="school" years={years} classes={classes} onApplied={vi.fn()} />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Source class"), "class1");
    await user.click(screen.getByRole("button", { name: "Load students" }));
    expect(await screen.findByText("Ama")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Source class"), "class2");
    expect(screen.queryByText("Ama")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load students" }));
    expect(await screen.findByText("No active students are enrolled in the selected class.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Promote from"), "year2");
    expect(screen.getByLabelText("Source class")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Load students" })).toBeDisabled();
    expect(within(screen.getByLabelText("Source class")).queryByRole("option", { name: "Basic 1 A" })).not.toBeInTheDocument();
  });

  it("warns when applying succeeded but refreshing failed", async () => {
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST"
      ? Response.json({ promote: 1, repeat: 0, graduate: 0, leave: 0 })
      : Response.json({ target_classes: [{ id: "class3", name: "Basic 2 A" }], rows: [row("one", "Ama")] }));
    render(<PromotionPanel authFetch={fetch} tenantId="school" years={years} classes={classes} onApplied={vi.fn().mockRejectedValue(new Error("offline"))} />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Source class"), "class1");
    await user.click(screen.getByRole("button", { name: "Load students" }));
    await user.click(await screen.findByRole("button", { name: "Apply to 1 student" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Promotion was applied, but the workspace could not be refreshed.");
    expect(screen.queryByRole("button", { name: /Apply to/ })).not.toBeInTheDocument();
  });

  it("allows an explicit school-wide current-year change", async () => {
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST"
      ? Response.json({ promote: 1, repeat: 0, graduate: 0, leave: 0 })
      : Response.json({ target_classes: [{ id: "class3", name: "Basic 2 A" }], rows: [row("one", "Ama")] }));
    render(<PromotionPanel authFetch={fetch} tenantId="school" years={years} classes={classes} onApplied={vi.fn(async () => {})} />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Source class"), "class1");
    await user.click(screen.getByRole("button", { name: "Load students" }));
    await user.click(await screen.findByRole("checkbox", { name: /Make the new year/ }));
    await user.click(screen.getByRole("button", { name: "Apply to 1 student" }));
    expect(await screen.findByRole("status")).toHaveTextContent("1 promoted");
    const request = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(request?.[1]?.body)).make_current).toBe(true);
  });
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import EnrollmentProvenance from "./enrollment-provenance";

const props = {
  tenantId: "school",
  terms: [{ id: "term", academic_year: "year", name: "Term 1", start_date: "2026-01-01", end_date: "2026-04-30", is_current: true, is_completed: false }],
  classes: [{ id: "class", academic_year: "year", name: "Basic 1", section: "A", room: "" }],
  subjects: [{ id: "subject", name: "Maths", code: "MATH" }],
};
const row = { enrollment: "one", student: "Ama", admission_number: "A1", segment_start: "2026-01-05",
  segment_end: "2026-03-19", fingerprint: "a".repeat(64), issues: ["campus_requires_attestation"],
  blocking: [], original: { campus: null, subjects: [], class_subjects: ["subject"] }, attestation: null, attestation_history: [] };

it("requires explicit evidence and reports a saved attestation without claiming reports are enabled", async () => {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("report/")) return Response.json({ students: [row], campuses: [{ id: "campus", name: "North" }] });
    expect(JSON.parse(String(init?.body))).toEqual({
      term: "term", enrollment: "one", previous_attestation: null, fingerprint: "a".repeat(64), segment_start: "2026-01-05",
      segment_end: "2026-03-19", campus: null, no_campus: true, subjects: ["subject"],
      reason: "Register", evidence: "Page 12",
    });
    return Response.json({ id: "attestation", version: 1 }, { status: 201 });
  });
  render(<EnrollmentProvenance {...props} authFetch={fetch} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Reconcile historical enrollment provenance"));
  await user.selectOptions(screen.getByLabelText("Reconciliation term"), "term");
  await user.selectOptions(screen.getByLabelText("Reconciliation class"), "class");
  await user.click(screen.getByRole("button", { name: "Load reconciliation report" }));
  expect(await screen.findByText(/Unattested/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("Confirm segment start"), "2026-01-05");
  await user.type(screen.getByLabelText("Confirm segment end"), "2026-03-19");
  await user.selectOptions(screen.getByLabelText("Verified campus"), "none");
  await user.click(screen.getByLabelText("Maths · MATH"));
  await user.type(screen.getByLabelText("Reconciliation reason"), "Register");
  await user.type(screen.getByLabelText("Supporting evidence or reference"), "Page 12");
  await user.click(screen.getByRole("button", { name: "Attest Ama" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Transfer report creation is still unavailable");
});

it("displays conflicts and blocks attestation", async () => {
  const fetch = vi.fn(async () => Response.json({
    students: [{ ...row, blocking: ["overlapping_enrollments"] }], campuses: [],
  }));
  render(<EnrollmentProvenance {...props} authFetch={fetch} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Reconcile historical enrollment provenance"));
  await user.selectOptions(screen.getByLabelText("Reconciliation term"), "term");
  await user.selectOptions(screen.getByLabelText("Reconciliation class"), "class");
  await user.click(screen.getByRole("button", { name: "Load reconciliation report" }));
  expect(await screen.findByText(/overlapping_enrollments/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Attest Ama" })).toBeDisabled();
});

it("requires a reason and confirmation to revoke and shows the recorded revocation", async () => {
  const attestation = { id: "attestation", version: 1, current: true, reason: "Verified", evidence: "Register",
    actor: "owner", timestamp: "2026-03-20", revocation: null };
  let revoked = false;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("report/")) return Response.json({ students: [{
      ...row, attestation: { ...attestation, current: !revoked,
        revocation: revoked ? { reason: "Wrong record", actor: "admin", timestamp: "2026-03-21" } : null },
    }], campuses: [] });
    expect(path).toContain("/attestation/revoke/");
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "Wrong record" });
    revoked = true;
    return Response.json({ id: "revocation", attestation: "attestation" }, { status: 201 });
  });
  render(<EnrollmentProvenance {...props} authFetch={fetch} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Reconcile historical enrollment provenance"));
  await user.selectOptions(screen.getByLabelText("Reconciliation term"), "term");
  await user.selectOptions(screen.getByLabelText("Reconciliation class"), "class");
  await user.click(screen.getByRole("button", { name: "Load reconciliation report" }));
  await screen.findByText(/current source/);
  await user.type(screen.getByLabelText("Revocation reason"), "Wrong record");
  await user.click(screen.getByRole("button", { name: "Revoke Ama attestation" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  await user.click(screen.getByLabelText("Confirm revocation of attestation v1"));
  await user.click(screen.getByRole("button", { name: "Revoke Ama attestation" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Published reports remain unchanged");
  expect(await screen.findByText(/Attestation v1: revoked/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Revoke Ama attestation" })).not.toBeInTheDocument();
});

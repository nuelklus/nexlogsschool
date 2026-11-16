"use client";

import { useState } from "react";

import { requestError, type AuthFetch } from "../school/student-api";

type Candidate = { id: string; name: string };
type ClassRow = { id: string; name: string; section: string; academic_year: string; candidates: Candidate[] };
type StudentRow = {
  id: string;
  name: string;
  admission_number: string;
  grade_level: string;
  issue: "no_current_enrollment" | "multiple_current_enrollments" | "grade_mismatch";
  classes: { id: string; name: string; section: string }[];
};
type Report = { current_year: { id: string; name: string } | null; unmapped_classes: ClassRow[]; student_issues: StudentRow[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCandidate(value: unknown): value is Candidate {
  return isRecord(value) && typeof value.id === "string" && typeof value.name === "string";
}

function isReport(value: unknown): value is Report {
  if (!isRecord(value) || !(value.current_year === null || isCandidate(value.current_year))
    || !Array.isArray(value.unmapped_classes) || !Array.isArray(value.student_issues)) return false;
  return value.unmapped_classes.every((row: unknown) =>
    isRecord(row) && typeof row.id === "string" && typeof row.name === "string" && typeof row.section === "string"
      && typeof row.academic_year === "string" && Array.isArray(row.candidates) && row.candidates.every(isCandidate))
    && value.student_issues.every((row: unknown) =>
      isRecord(row) && typeof row.id === "string" && typeof row.name === "string" && typeof row.admission_number === "string"
      && typeof row.grade_level === "string"
      && (row.issue === "no_current_enrollment" || row.issue === "multiple_current_enrollments" || row.issue === "grade_mismatch")
      && Array.isArray(row.classes) && row.classes.every((item: unknown) =>
        isRecord(item) && typeof item.id === "string" && typeof item.name === "string" && typeof item.section === "string"));
}

const issueLabels: Record<StudentRow["issue"], string> = {
  no_current_enrollment: "No active enrollment in the current year",
  multiple_current_enrollments: "Multiple active enrollments in the current year",
  grade_mismatch: "Student grade differs from the enrolled class",
};

export default function ClassReconciliation({ authFetch, tenantId }: { authFetch: AuthFetch; tenantId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError("");
    try {
      const response = await authFetch(`/tenants/${tenantId}/academics/class-reconciliation/`);
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw requestError(response, payload);
      if (!isReport(payload)) throw new Error("The server returned an unexpected reconciliation report.");
      setReport(payload);
    } catch (caught) {
      setReport(null);
      setError(caught instanceof Error ? caught.message : "The reconciliation report could not be loaded.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Legacy class reconciliation">
      <h2>Review legacy classes and student grades</h2>
      <p className="fin-note">This read-only report suggests possible level matches. Review them before assigning a level above; nothing is changed automatically. Student checks use the current academic year only.</p>
      <button className="secondary-action" type="button" disabled={busy} onClick={() => void load()}>
        {busy ? "Loading…" : report ? "Refresh reconciliation" : "Load reconciliation"}
      </button>
      {error && <p className="setup-error" role="alert">{error}</p>}
      {report && (
        <>
          <h3>Unmapped classes ({report.unmapped_classes.length})</h3>
          {report.unmapped_classes.length === 0 ? <p>No unmapped classes.</p> : (
            <ul className="academic-records">
              {report.unmapped_classes.map((row) => (
                <li key={row.id}>
                  <strong>{row.name}{row.section ? ` · ${row.section}` : ""} · {row.academic_year}</strong>
                  <span>{row.candidates.length ? `Possible levels: ${row.candidates.map((item) => item.name).join(", ")}` : "No matching configured level; review manually."}</span>
                </li>
              ))}
            </ul>
          )}
          <h3>Student enrollment checks</h3>
          {!report.current_year ? <p>No current academic year is set; student checks have not run.</p>
            : report.student_issues.length === 0 ? <p>No issues found for {report.current_year.name}.</p>
              : (
                <ul className="academic-records">
                  {report.student_issues.map((row) => (
                    <li key={row.id}>
                      <strong>{row.name} · {row.admission_number}</strong>
                      <span>{issueLabels[row.issue]} · Student grade: {row.grade_level || "Not set"}{row.classes.length ? ` · Class: ${row.classes.map((item) => `${item.name}${item.section ? ` ${item.section}` : ""}`).join(", ")}` : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
        </>
      )}
    </section>
  );
}

"use client";

import { FormEvent, useState } from "react";
import { requestError, type AuthFetch } from "../school/student-api";
import type { ExamDetail, Examination } from "./examination-api";
import type { Subject } from "./academics-api";

type Student = { enrollment: string; name: string; admission_number: string };
type Roster = { year: string; term: string; class_name: string; campus: string | null; students: Student[] };
type Props = { authFetch: AuthFetch; tenantId: string; detail: ExamDetail; exams: Examination[]; subjects: Subject[]; onReviewed: () => Promise<void> };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
function errorMessages(value: unknown, field = ""): string[] {
  if (typeof value === "string") return [field && field !== "detail" && field !== "non_field_errors" ? `${field.replaceAll("_", " ")}: ${value}` : value];
  if (Array.isArray(value) && value.length) return value.flatMap((item) => errorMessages(item, field));
  if (record(value) && Object.keys(value).length) return Object.entries(value).flatMap(([key, item]) =>
    errorMessages(item, key === "detail" || key === "non_field_errors" ? field : [field, key].filter(Boolean).join(" · ")));
  return [field ? `${field.replaceAll("_", " ")}: Unrecognized error from the server.` : "Unrecognized error from the server."];
}
function isRoster(value: unknown): value is Roster {
  return record(value) && typeof value.year === "string" && typeof value.term === "string"
    && typeof value.class_name === "string" && (value.campus === null || typeof value.campus === "string")
    && Array.isArray(value.students) && value.students.every((row: unknown) => record(row)
      && typeof row.enrollment === "string" && typeof row.name === "string" && typeof row.admission_number === "string");
}

export default function ReportCardBatch({ authFetch, tenantId, detail, exams, subjects, onReviewed }: Props) {
  const [rosterDate, setRosterDate] = useState(detail.start_date);
  const [roster, setRoster] = useState<Roster | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rowErrors, setRowErrors] = useState<{ admission_number: string; errors: unknown }[]>([]);
  const [message, setMessage] = useState("");
  const eligible = exams.filter((exam) => exam.term === detail.term && exam.school_class === detail.school_class && exam.published_version > 0);

  async function send(path: string, data: unknown): Promise<unknown> {
    const response = await authFetch(`/tenants/${tenantId}/academics/report-cards/${path}/`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    const result: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      if (record(result) && Array.isArray(result.students)) {
        setRowErrors(result.students.map((item: unknown, index: number) => ({
          admission_number: record(item) && typeof item.admission_number === "string" ? item.admission_number : `Unknown student ${index + 1}`,
          errors: record(item) ? item.errors : undefined,
        })));
      }
      throw requestError(response, result);
    }
    return result;
  }
  async function loadRoster() {
    setBusy(true); setError(""); setMessage(""); setRowErrors([]); setRoster(null);
    try {
      const result = await send("class-roster", { term: detail.term, school_class: detail.school_class, roster_date: rosterDate });
      if (!isRoster(result)) throw new Error("Unexpected class roster response.");
      setRoster(result);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load the roster."); }
    finally { setBusy(false); }
  }
  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!roster || busy) return;
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true); setError(""); setMessage(""); setRowErrors([]);
    try {
      const result = await send("class-review", {
        term: detail.term, school_class: detail.school_class, roster_date: rosterDate,
        entries: roster.students.map((student) => ({
          enrollment: student.enrollment, examinations: formData.getAll(`exams-${student.enrollment}`),
          class_teacher_remark: formData.get(`teacher-${student.enrollment}`),
          headteacher_remark: formData.get(`head-${student.enrollment}`),
          reason: formData.get(`reason-${student.enrollment}`),
        })),
      });
      if (!record(result) || result.count !== roster.students.length || !Array.isArray(result.reports)
          || result.reports.length !== roster.students.length) throw new Error("Unexpected review response. Reload report cards before retrying.");
      setRoster(null);
      setMessage(`${result.count} reports reviewed; each still requires administrator publication.`);
      try {
        await onReviewed();
      } catch {
        setError("Reports were reviewed, but the report list could not be refreshed. Reload it before continuing.");
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not review the class."); }
    finally { setBusy(false); }
  }
  return <details>
    <summary>Review class report cards</summary>
    <p>Review every student in the dated roster. Select published results and enter individual remarks. If any student fails validation, no reports are saved. Transferred students are currently rejected for manual reconciliation.</p>
    <label>Review roster date <input type="date" value={rosterDate} disabled={busy} onChange={(event) => {
      setRosterDate(event.target.value); setRoster(null); setRowErrors([]);
    }} /></label>
    <button type="button" disabled={busy || !rosterDate} onClick={() => void loadRoster()}>Load review roster</button>
    {error && <p role="alert" className="setup-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    {rowErrors.length > 0 && <div className="exam-notice">
      <h4>Student review errors</h4>
      <p>If a reviewed version already awaits publication, an owner or administrator can load and publish it instead of reviewing again.</p>
      <ul aria-label="Student review errors">{rowErrors.map((row) => {
        const messages = errorMessages(row.errors);
        return <li key={row.admission_number}><strong>{row.admission_number}:</strong> {messages.length ? messages.join("; ") : "The server returned an unrecognized error. Check the response or contact support."}</li>;
      })}</ul>
    </div>}
    {roster && <form className="academic-form" onSubmit={review}>
      <p>{roster.year} · {roster.term} · {roster.class_name} · {roster.campus ?? "No campus assigned"} · {roster.students.length} students</p>
      {roster.students.map((student) => <fieldset key={student.enrollment}>
        <legend>{student.name} · {student.admission_number}</legend>
        {eligible.map((exam) => <label key={exam.id}><input type="checkbox" name={`exams-${student.enrollment}`} value={exam.id} />
          {exam.name} · {subjects.find((subject) => subject.id === exam.subject)?.name ?? exam.subject} · v{exam.published_version}</label>)}
        <label>Class teacher remark <textarea name={`teacher-${student.enrollment}`} maxLength={1000} required /></label>
        <label>Headteacher remark <textarea name={`head-${student.enrollment}`} maxLength={1000} required /></label>
        <label>Revision reason <input name={`reason-${student.enrollment}`} maxLength={500} /></label>
      </fieldset>)}
      <button type="submit" disabled={busy || !eligible.length}>Review all reports</button>
    </form>}
  </details>;
}

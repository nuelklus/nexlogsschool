"use client";

import { FormEvent, useState } from "react";
import { requestError, type AuthFetch } from "../school/student-api";
import type { AcademicTerm, SchoolClass, Subject } from "./academics-api";

type Row = {
  enrollment: string; student: string; admission_number: string; segment_start: string; segment_end: string;
  fingerprint: string; issues: string[]; blocking: string[]; original: Record<string, unknown>;
  attestation: null | { id: string; version: number; current: boolean; reason: string; evidence: string; actor: string; timestamp: string;
    revocation: null | { reason: string; actor: string; timestamp: string } };
  attestation_history: Record<string, unknown>[];
};
type Report = { students: Row[]; campuses: { id: string; name: string }[] };
type Props = { authFetch: AuthFetch; tenantId: string; terms: AcademicTerm[]; classes: SchoolClass[]; subjects: Subject[] };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const isRevocation = (value: unknown): boolean => value === null || (record(value)
  && ["reason", "actor", "timestamp"].every((key) => typeof value[key] === "string"));
function isReport(value: unknown): value is Report {
  return record(value) && Array.isArray(value.campuses)
    && value.campuses.every((c: unknown) => record(c) && typeof c.id === "string" && typeof c.name === "string")
    && Array.isArray(value.students) && value.students.every((row: unknown) => record(row)
      && ["enrollment", "student", "admission_number", "segment_start", "segment_end", "fingerprint"]
        .every((key) => typeof row[key] === "string")
      && strings(row.issues) && strings(row.blocking) && record(row.original)
      && Array.isArray(row.attestation_history) && row.attestation_history.every(record)
      && (row.attestation === null || (record(row.attestation) && typeof row.attestation.id === "string"
        && typeof row.attestation.version === "number" && typeof row.attestation.current === "boolean"
        && isRevocation(row.attestation.revocation)
        && ["reason", "evidence", "actor", "timestamp"].every((key) => typeof (row.attestation as Record<string, unknown>)[key] === "string"))));
}

export default function EnrollmentProvenance({ authFetch, tenantId, terms, classes, subjects }: Props) {
  const [term, setTerm] = useState("");
  const [schoolClass, setSchoolClass] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function send(path: string, data: unknown): Promise<unknown> {
    const response = await authFetch(`/tenants/${tenantId}/academics/enrollment-provenance/${path}/`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw requestError(response, payload);
    return payload;
  }
  async function load() {
    const value = await send("report", { term, school_class: schoolClass });
    if (!isReport(value)) throw new Error("Unexpected reconciliation report response.");
    setReport(value);
  }
  async function loadReport() {
    setBusy(true); setError(""); setMessage(""); setReport(null);
    try { await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load the reconciliation report."); }
    finally { setBusy(false); }
  }
  async function attest(event: FormEvent<HTMLFormElement>, row: Row) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const campus = data.get("campus");
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await send("attest", {
        term, enrollment: row.enrollment, fingerprint: row.fingerprint,
        previous_attestation: row.attestation?.id ?? null,
        segment_start: data.get("segment_start"), segment_end: data.get("segment_end"),
        campus: campus === "none" ? null : campus, no_campus: campus === "none",
        subjects: data.getAll("subjects"), reason: data.get("reason"), evidence: data.get("evidence"),
      });
      if (!record(result) || typeof result.id !== "string" || typeof result.version !== "number") {
        throw new Error("Unexpected attestation response. Reload before retrying.");
      }
      setMessage(`Attestation v${result.version} saved. Transfer report creation is still unavailable.`);
      setReport(null);
      try { await load(); }
      catch { setError("Attestation was saved, but the report could not be refreshed. Reload before continuing."); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save attestation."); }
    finally { setBusy(false); }
  }
  async function revoke(event: FormEvent<HTMLFormElement>, row: Row) {
    event.preventDefault();
    if (!row.attestation || row.attestation.revocation) return;
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await send(`${row.attestation.id}/revoke`, { reason: data.get("reason") });
      if (!record(result) || typeof result.id !== "string" || result.attestation !== row.attestation.id) {
        throw new Error("Unexpected revocation response. Reload before retrying.");
      }
      setMessage(`Attestation v${row.attestation.version} revoked. Published reports remain unchanged.`);
      setReport(null);
      try { await load(); }
      catch { setError("Revocation was saved, but the report could not be refreshed. Reload before continuing."); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not revoke attestation."); }
    finally { setBusy(false); }
  }
  return <details>
    <summary>Reconcile historical enrollment provenance</summary>
    <p>Inspect original records and verify evidence before attesting. This does not change enrollment, marks or published reports, and does not yet enable transfer reports.</p>
    <label>Reconciliation term <select value={term} onChange={(e) => { setTerm(e.target.value); setSchoolClass(""); setReport(null); }}>
      <option value="">Select term</option>{terms.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label>Reconciliation class <select value={schoolClass} onChange={(e) => { setSchoolClass(e.target.value); setReport(null); }}>
      <option value="">Select class</option>{classes.filter((item) => item.academic_year === terms.find((t) => t.id === term)?.academic_year)
        .map((item) => <option key={item.id} value={item.id}>{item.name} {item.section}</option>)}
    </select></label>
    <button type="button" disabled={busy || !term || !schoolClass} onClick={() => void loadReport()}>Load reconciliation report</button>
    {error && <p role="alert" className="setup-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    {report && report.students.length === 0 && <p>No enrollments overlap this term.</p>}
    {report?.students.map((row) => <section key={row.enrollment}>
      <h4>{row.student} · {row.admission_number}</h4>
      <p>Recorded segment: {row.segment_start} to {row.segment_end}. {row.attestation
        ? `Attestation v${row.attestation.version}: ${row.attestation.revocation ? "revoked" : row.attestation.current ? "current source" : "source changed"}.`
        : "Unattested."}</p>
      {row.attestation && <p>Actor {row.attestation.actor} · {row.attestation.timestamp} · {row.attestation.reason} · {row.attestation.evidence}</p>}
      {row.attestation?.revocation && <p>Revoked by {row.attestation.revocation.actor} · {row.attestation.revocation.timestamp} · {row.attestation.revocation.reason}</p>}
      {row.attestation && !row.attestation.revocation && <form className="academic-form" onSubmit={(event) => void revoke(event, row)}>
        <p>Revocation is permanent. Previously published reports remain unchanged and require separate review.</p>
        <label>Revocation reason <input name="reason" maxLength={500} required /></label>
        <label><input type="checkbox" required /> Confirm revocation of attestation v{row.attestation.version}</label>
        <button type="submit" disabled={busy}>Revoke {row.student} attestation</button>
      </form>}
      <p>Requires review: {row.issues.join(", ")}{row.blocking.length ? `; blocked: ${row.blocking.join(", ")}` : ""}</p>
      <details><summary>Inspect original records</summary><pre>{JSON.stringify(row.original, null, 2)}</pre></details>
      {row.attestation_history.length > 0 && <details><summary>Inspect attestation history</summary><pre>{JSON.stringify(row.attestation_history, null, 2)}</pre></details>}
      <form className="academic-form" onSubmit={(event) => void attest(event, row)}>
        <label>Confirm segment start <input type="date" name="segment_start" required /></label>
        <label>Confirm segment end <input type="date" name="segment_end" required /></label>
        <label>Verified campus <select name="campus" required defaultValue=""><option value="">Select based on evidence</option>
          <option value="none">Confirmed no campus</option>{report.campuses.map((campus) => <option key={campus.id} value={campus.id}>{campus.name}</option>)}
        </select></label>
        <fieldset><legend>Confirm all subjects currently assigned to this class</legend>{subjects.filter((subject) =>
          strings(row.original.class_subjects) && row.original.class_subjects.includes(subject.id)).map((subject) =>
          <label key={subject.id}><input type="checkbox" name="subjects" value={subject.id} />{subject.name} · {subject.code}</label>)}</fieldset>
        <label>Reconciliation reason <input name="reason" maxLength={500} required /></label>
        <label>Supporting evidence or reference <input name="evidence" maxLength={1000} required /></label>
        <button type="submit" disabled={busy || row.blocking.length > 0}>Attest {row.student}</button>
      </form>
    </section>)}
  </details>;
}

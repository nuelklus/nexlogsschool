"use client";

import { FormEvent, useState } from "react";
import type { AuthFetch } from "../school/student-api";
import type { AcademicTerm, SchoolClass, Subject } from "./academics-api";
import { createExamSession, listExamSessions, type Configuration, type ExamSession } from "./examination-api";

type Props = {
  authFetch: AuthFetch; tenantId: string; canManage: boolean; terms: AcademicTerm[];
  classes: SchoolClass[]; subjects: Subject[]; configs: Configuration[];
  onCreated: () => Promise<void>; onOpen: (id: string) => Promise<void>;
  canOpen: boolean;
};

export default function ExaminationSessions({ authFetch, tenantId, canManage, terms, classes, subjects, configs, onCreated, onOpen, canOpen }: Props) {
  const [rows, setRows] = useState([0]);
  const [nextId, setNextId] = useState(1);
  const [sessions, setSessions] = useState<ExamSession[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await operation(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The request failed."); }
    finally { setBusy(false); }
  }
  async function refresh() {
    setSessions(await listExamSessions(authFetch, tenantId));
    setLoaded(true);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const entries = rows.map((row) => Object.fromEntries(
      ["school_class", "subject", "configuration", "start_date", "end_date", "marks_deadline", "publication_date"]
        .map((field) => [field, data.get(`${field}-${row}`)]),
    ));
    await run(async () => {
      await createExamSession(authFetch, tenantId, { name: data.get("name"), term: data.get("term"), entries });
      form.reset(); setRows([0]); setNextId(1);
      try {
        await Promise.all([onCreated(), refresh()]);
        setMessage("Examination session created.");
      } catch {
        setError("The session was created, but the lists could not be refreshed. Reload before creating it again.");
      }
    });
  }

  return <section aria-label="Examination sessions">
    <h3>Multi-class examination sessions</h3>
    <p>Each class and subject has its own schedule, grading configuration and approval workflow. Teachers see only their assigned entries.</p>
    {canManage && <p className="exam-notice">A session creates new exams; it does not add existing exams. Leave out subjects already created unless you intend another exam.</p>}
    {error && <p role="alert" className="setup-error">{error}</p>}
    {message && <p role="status" className="academic-success">{message}</p>}
    {canManage && <details>
      <summary>Create examination session</summary>
      <form className="academic-form" onSubmit={submit}>
        <label>Session name <input name="name" required maxLength={120} /></label>
        <label>Session term <select name="term" required defaultValue=""><option value="">Select term</option>{terms.map((term) => <option key={term.id} value={term.id}>{term.name}</option>)}</select></label>
        {rows.map((row, index) => <fieldset key={row}>
          <legend>Class and subject {index + 1}</legend>
          <label>Class <select name={`school_class-${row}`} required defaultValue=""><option value="">Select class</option>{classes.filter((item) => item.is_active !== false).map((item) => <option key={item.id} value={item.id}>{item.name} {item.section}</option>)}</select></label>
          <label>Subject <select name={`subject-${row}`} required defaultValue=""><option value="">Select subject</option>{subjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Grading configuration <select name={`configuration-${row}`} required defaultValue=""><option value="">Select configuration</option>{configs.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version} · {item.examination_type}</option>)}</select></label>
          <label>Start date <input name={`start_date-${row}`} type="date" required /></label>
          <label>End date <input name={`end_date-${row}`} type="date" required /></label>
          <label>Marks deadline <input name={`marks_deadline-${row}`} type="date" required /></label>
          <label>Publication date <input name={`publication_date-${row}`} type="date" required /></label>
          {rows.length > 1 && <button type="button" onClick={() => setRows(rows.filter((item) => item !== row))}>Remove class and subject {index + 1}</button>}
        </fieldset>)}
        <button type="button" disabled={rows.length >= 200} onClick={() => { setRows([...rows, nextId]); setNextId(nextId + 1); }}>Add class and subject</button>
        <button type="submit" disabled={busy}>Create session</button>
      </form>
    </details>}
    <button type="button" disabled={busy} onClick={() => void run(refresh)}>Load session progress</button>
    {loaded && sessions.length === 0 && <p>No accessible examination sessions.</p>}
    {sessions.map((session) => <div key={session.id}>
      <h4>{session.name}</h4>
      <p>Visible entries: {session.entries.length} · Draft: {session.counts.draft} · Submitted: {session.counts.submitted} · Approved: {session.counts.approved} · Published: {session.counts.published}</p>
      <ul>{session.entries.map((entry) => <li key={entry.id}>
        {entry.class_name} {entry.section} · {entry.subject_name} · {entry.start_date}–{entry.end_date} · {entry.status}
        <button type="button" disabled={busy || !canOpen} onClick={() => void run(() => onOpen(entry.id))}>Open {entry.class_name} {entry.subject_name}</button>
      </li>)}</ul>
    </div>)}
  </section>;
}

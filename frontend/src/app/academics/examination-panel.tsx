"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { AuthFetch } from "../school/student-api";
import type { AcademicTerm, SchoolClass, Subject } from "./academics-api";
import ExaminationSessions from "./examination-sessions";
import ReportCardPreview from "./report-card-preview";
import ReportCards from "./report-cards";
import EnrollmentProvenance from "./enrollment-provenance";
import GradingConfigurations from "./grading-configurations";
import {
  createExam, examAction, getExam, listExamConfigs, listExams, saveMarks,
  type Configuration, type ExamDetail, type Examination, type ExamMark, type MarkStatus,
} from "./examination-api";

type Props = {
  authFetch: AuthFetch; tenantId: string; role: string;
  terms: AcademicTerm[]; classes: SchoolClass[]; subjects: Subject[];
};

export default function ExaminationPanel({ authFetch, tenantId, role, terms, classes, subjects }: Props) {
  const canManage = role === "owner" || role === "school_admin";
  const [configs, setConfigs] = useState<Configuration[]>([]);
  const [exams, setExams] = useState<Examination[]>([]);
  const [examsLoaded, setExamsLoaded] = useState(false);
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [entries, setEntries] = useState<ExamMark[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [dirty, setDirty] = useState(false);
  const [termFilter, setTermFilter] = useState("");
  const [classFilter, setClassFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const focusOnOpen = useRef(false);

  const load = useCallback(async () => {
    const [configurations, examinations] = await Promise.all([
      listExamConfigs(authFetch, tenantId), listExams(authFetch, tenantId),
    ]);
    setConfigs(configurations);
    setExams(examinations);
    setExamsLoaded(true);
  }, [authFetch, tenantId]);
  useEffect(() => {
    let active = true;
    Promise.all([listExamConfigs(authFetch, tenantId), listExams(authFetch, tenantId)])
      .then(([configurations, examinations]) => { if (active) { setConfigs(configurations); setExams(examinations); setExamsLoaded(true); } })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "Could not load examinations."); });
    return () => { active = false; };
  }, [authFetch, tenantId]);

  async function open(id: string, focus = false) {
    const next = await getExam(authFetch, tenantId, id);
    focusOnOpen.current = focus;
    setDetail(next);
    setDirty(false);
    setEntries(next.students.flatMap((student) => next.policy.components.map((component) =>
      next.marks.find((mark) => mark.enrollment === student.enrollment && mark.component === component.key)
      ?? { enrollment: student.enrollment, component: component.key, status: "missing", score: null, remarks: "" },
    )));
  }
  async function openFromList(id: string) {
    await open(id, true);
  }
  useEffect(() => {
    if (!detail || !focusOnOpen.current) return;
    focusOnOpen.current = false;
    detailRef.current?.focus({ preventScroll: true });
    detailRef.current?.scrollIntoView?.({ block: "start" });
  }, [detail]);
  async function run(operation: () => Promise<void>, success: string) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await operation(); setMessage(success); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The request failed."); }
    finally { setBusy(false); }
  }
  function setMark(enrollment: string, component: string, changes: Partial<ExamMark>) {
    setDirty(true);
    setEntries((old) => old.map((entry) => entry.enrollment === enrollment && entry.component === component
      ? { ...entry, ...changes } : entry));
  }
  async function addExam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    setBusy(true); setError(""); setMessage("");
    try {
      await createExam(authFetch, tenantId, data);
      form.reset();
      try {
        await load();
        setMessage("Examination created.");
      } catch {
        setError("The examination was created, but the list could not be refreshed. Refresh the list before creating it again.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The request failed.");
    } finally { setBusy(false); }
  }
  const visibleExams = exams.filter((exam) =>
    (!termFilter || exam.term === termFilter) && (!classFilter || exam.school_class === classFilter)
    && (!statusFilter || exam.status === statusFilter));

  return <section className="academic-card exam-workspace" aria-label="Managed examination workflow">
    <h2>Managed examinations</h2>
    <p>Create one exam per class and subject, not per student. A session creates several new entries together.</p>
    <ol className="exam-workflow" aria-label="Examination workflow">
      <li><strong>1. Set up</strong><span>Owner or administrator creates the exam.</span></li>
      <li><strong>2. Enter marks</strong><span>Assigned teacher saves and submits.</span></li>
      <li><strong>3. Review</strong><span>Headteacher approves or returns.</span></li>
      <li><strong>4. Publish</strong><span>Owner or administrator publishes results.</span></li>
    </ol>
    {error && <p className="setup-error" role="alert">{error}</p>}
    {message && <p className="academic-success" role="status">{message}</p>}
    <section aria-label="Created examinations">
      <h3>Created examinations</h3>
      <p>{role === "teacher" ? "Examinations assigned to you are shown below." : "Examinations for this school, including session entries, are shown below."}</p>
      <button type="button" disabled={busy} onClick={() => void run(load, "Examination list refreshed.")}>Refresh examinations</button>
      {examsLoaded && <p>{exams.length === 0 ? "No managed examinations have been created or are accessible to you." : `${exams.length} examination${exams.length === 1 ? "" : "s"} listed.`}</p>}
      {exams.length > 0 && <div className="exam-filters">
        <label>Filter by term <select value={termFilter} onChange={(event) => setTermFilter(event.target.value)}><option value="">All terms</option>{terms.map((term) => <option key={term.id} value={term.id}>{term.name}</option>)}</select></label>
        <label>Filter by class <select value={classFilter} onChange={(event) => setClassFilter(event.target.value)}><option value="">All classes</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.name} {item.section}</option>)}</select></label>
        <label>Filter by status <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option><option value="draft">Draft</option><option value="submitted">Submitted</option><option value="approved">Approved</option><option value="published">Published</option></select></label>
        <p aria-live="polite">Showing {visibleExams.length} of {exams.length} examinations.</p>
      </div>}
      {exams.length > 0 && visibleExams.length === 0 && <p>No examinations match these filters.</p>}
      {dirty && <p className="exam-notice">Save your draft marks before opening another examination.</p>}
      <ul className="academic-records">{visibleExams.map((exam) => {
        const schoolClass = classes.find((item) => item.id === exam.school_class);
        const term = terms.find((item) => item.id === exam.term);
        const subject = subjects.find((item) => item.id === exam.subject);
        return <li key={exam.id}>
          <strong>{exam.name}</strong>
          <span>Term: {term?.name ?? exam.term} · Class: {schoolClass ? [schoolClass.name, schoolClass.section].filter(Boolean).join(" ") : exam.school_class} · Subject: {subject?.name ?? exam.subject}</span>
          <span>Dates: {exam.start_date}–{exam.end_date} · Status: {exam.status} · Version: {exam.version}</span>
          <button type="button" disabled={busy || dirty} aria-label={`Open ${exam.name}, ${schoolClass?.name ?? exam.school_class}, ${subject?.name ?? exam.subject}`} onClick={() => void run(() => openFromList(exam.id), "Examination loaded.")}>Open</button>
        </li>;
      })}</ul>
    </section>
    {canManage && <EnrollmentProvenance authFetch={authFetch} tenantId={tenantId} terms={terms} classes={classes} subjects={subjects} />}
    <GradingConfigurations authFetch={authFetch} tenantId={tenantId} canManage={canManage} configs={configs} onSaved={load} />
    {canManage && <>
      <details>
        <summary>Create examination</summary>
        <form className="academic-form" onSubmit={addExam}>
          <label>Name <input name="name" required /></label>
          <label>Term <select name="term" required defaultValue=""><option value="">Select term</option>{terms.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Class <select name="school_class" required defaultValue=""><option value="">Select class</option>{classes.filter((item) => item.is_active !== false).map((item) => <option key={item.id} value={item.id}>{item.name} {item.section}</option>)}</select></label>
          <label>Subject <select name="subject" required defaultValue=""><option value="">Select subject</option>{subjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Grading configuration <select name="configuration" required defaultValue=""><option value="">Select configuration</option>{configs.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version} · {item.examination_type}</option>)}</select></label>
          <label>Start date <input name="start_date" type="date" required /></label>
          <label>End date <input name="end_date" type="date" required /></label>
          <label>Marks deadline <input name="marks_deadline" type="date" required /></label>
          <label>Publication date <input name="publication_date" type="date" required /></label>
          <button type="submit" disabled={busy}>Create examination</button>
        </form>
      </details>
    </>}
    <ExaminationSessions authFetch={authFetch} tenantId={tenantId} canManage={canManage} terms={terms}
      classes={classes} subjects={subjects} configs={configs} onCreated={load} onOpen={openFromList} canOpen={!dirty && !busy} />
    {detail && <section ref={detailRef} tabIndex={-1} aria-label="Open examination" className="exam-detail">
      <h3>{detail.name} · {detail.status} · version {detail.version}</h3>
      <p>{classes.find((item) => item.id === detail.school_class)?.name ?? detail.school_class} · {subjects.find((item) => item.id === detail.subject)?.name ?? detail.subject} · {terms.find((item) => item.id === detail.term)?.name ?? detail.term}</p>
      <p className="exam-notice">{detail.status === "draft" ? "Draft: the assigned teacher enters and submits marks after the exam ends." : detail.status === "submitted" ? "Submitted: the headteacher can approve or return the results." : detail.status === "approved" ? "Approved: an owner or administrator can publish on or after the publication date." : detail.status === "published" ? "Published: results are visible to eligible students and parents." : `Current status: ${detail.status}.`}</p>
      {(canManage || role === "headteacher") && <ReportCardPreview key={detail.id} authFetch={authFetch}
        tenantId={tenantId} detail={detail} exams={exams} subjects={subjects} />}
      {(canManage || role === "headteacher") && <ReportCards key={`cards-${detail.id}`} authFetch={authFetch}
        tenantId={tenantId} role={role} detail={detail} exams={exams} subjects={subjects} />}
      <p>Marks deadline: {detail.marks_deadline} · Publication date: {detail.publication_date}</p>
      <h4>Student marks</h4>
      {detail.students.length === 0 && <p>No eligible students are enrolled in this class and subject on the examination start date.</p>}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>Student</th>{detail.policy.components.map((component) => <th key={component.key}>{component.key} (out of {component.maximum})</th>)}<th>Saved result</th></tr></thead>
          <tbody>{detail.students.map((student) => <tr key={student.enrollment}>
            <th>{student.name} · {student.admission_number}</th>
            {detail.policy.components.map((component) => {
              const mark = entries.find((item) => item.enrollment === student.enrollment && item.component === component.key);
              if (!mark) return <td key={component.key}>Loading</td>;
              const editable = role === "teacher" && detail.status === "draft";
              return <td key={component.key}>
                <label>Status for {student.name}, {component.key}
                  <select disabled={!editable} value={mark.status} onChange={(event) => {
                    const status = event.target.value as MarkStatus;
                    setMark(student.enrollment, component.key, { status, score: status === "scored" ? "" : null });
                  }}>
                    <option value="missing">Missing</option><option value="scored">Scored</option><option value="absent">Absent</option><option value="exempt">Exempt</option><option value="not_applicable">Not applicable</option>
                  </select>
                </label>
                {mark.status === "scored" && <label>Score for {student.name}, {component.key}
                  <input disabled={!editable} type="number" min="0" max={component.maximum} step={detail.policy.allow_decimal_scores ? "0.01" : "1"} value={mark.score ?? ""} onChange={(event) => setMark(student.enrollment, component.key, { score: event.target.value })} />
                </label>}
                <label>Remarks for {student.name}, {component.key}<input disabled={!editable} maxLength={240} value={mark.remarks} onChange={(event) => setMark(student.enrollment, component.key, { remarks: event.target.value })} /></label>
              </td>;
            })}
            <td>{(() => {
              const calculated = detail.calculations.find((item) => item.enrollment === student.enrollment);
              return calculated?.error ?? (calculated?.score !== null && calculated?.score !== undefined
                ? `${calculated.score}% · ${calculated.grade} · ${calculated.description}`
                : "Not calculated");
            })()}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {role === "teacher" && detail.status === "draft" && <>
        <button type="button" disabled={busy || !entries.length} onClick={() => void run(async () => {
          if (entries.some((entry) => entry.status === "scored" && !entry.score?.trim())) throw new Error("Enter a score for every scored mark.");
          await saveMarks(authFetch, tenantId, detail.id, entries); await open(detail.id);
        }, "Draft marks saved.")}>Save draft marks</button>
        {dirty && <p>Save your changes before submitting.</p>}
        <button type="button" disabled={busy || dirty} onClick={() => void run(async () => {
          await examAction(authFetch, tenantId, detail.id, "submit"); await open(detail.id); await load();
        }, "Results submitted.")}>Submit saved marks</button>
      </>}
      {role === "headteacher" && ["submitted", "approved"].includes(detail.status) && <>
        {detail.status === "submitted" && <button type="button" disabled={busy} onClick={() => void run(async () => {
          await examAction(authFetch, tenantId, detail.id, "approve"); await open(detail.id); await load();
        }, "Results approved.")}>Approve results</button>}
        <label>Reason for return <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} /></label>
        <button type="button" disabled={busy || !reason.trim()} onClick={() => void run(async () => {
          await examAction(authFetch, tenantId, detail.id, "return", reason); await open(detail.id); await load(); setReason("");
        }, "Results returned for correction.")}>Return for correction</button>
      </>}
      {canManage && detail.status === "approved" && <button type="button" disabled={busy} onClick={() => void run(async () => {
        await examAction(authFetch, tenantId, detail.id, "publish"); await open(detail.id); await load();
      }, "Results published.")}>Publish results</button>}
      {canManage && detail.status === "published" && <>
        <label>Reason for published correction <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} /></label>
        <button type="button" disabled={busy || !reason.trim()} onClick={() => void run(async () => {
          await examAction(authFetch, tenantId, detail.id, "correct", reason); await open(detail.id); await load(); setReason("");
        }, "Correction started. The previous publication remains visible until a new version is approved and published.")}>Start correction</button>
      </>}
      {detail.publication_history.length > 0 && <details>
        <summary>Published result history</summary>
        <ul>{detail.publication_history.map((item, index) => <li key={`${item.version}-${index}`}>
          Version {item.version} · {item.result.student_name} · {item.result.score}% · {item.result.grade} · {item.published_at}
        </li>)}</ul>
      </details>}
    </section>}
  </section>;
}

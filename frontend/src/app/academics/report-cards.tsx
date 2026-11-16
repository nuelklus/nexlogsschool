"use client";

import { FormEvent, useState } from "react";
import type { AuthFetch } from "../school/student-api";
import type { Subject } from "./academics-api";
import type { ExamDetail, Examination } from "./examination-api";
import { changeReport, downloadClassReports, downloadReport, listReportCards, type ReportCard } from "./report-card-api";
import ReportCardBatch from "./report-card-batch";

type Props = { authFetch: AuthFetch; tenantId: string; role: string; detail: ExamDetail; exams: Examination[]; subjects: Subject[] };

export default function ReportCards({ authFetch, tenantId, role, detail, exams, subjects }: Props) {
  const [reports, setReports] = useState<ReportCard[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [rosterDate, setRosterDate] = useState(detail.start_date);
  const eligible = exams.filter((exam) => exam.term === detail.term && exam.school_class === detail.school_class && exam.published_version > 0);

  async function refresh() {
    setReports(await listReportCards(authFetch, tenantId));
    setLoaded(true);
  }
  async function run(action: () => Promise<void>, success = "") {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await action(); setMessage(success); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The request failed."); }
    finally { setBusy(false); }
  }
  async function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await run(async () => {
      await changeReport(authFetch, tenantId, "", {
        term: detail.term, enrollment: data.get("enrollment"), examinations: data.getAll("examinations"),
        class_teacher_remark: data.get("class_teacher_remark"), headteacher_remark: data.get("headteacher_remark"),
        reason: data.get("reason"),
      });
      form.reset();
      await refresh();
    }, "Report reviewed and awaiting publication.");
  }
  return <section aria-label="Official report cards">
    <h3>Official report cards</h3>
    <ol className="exam-workflow" aria-label="Report card workflow">
      <li><strong>1. Publish subject results</strong><span>One published result is required per enrolled subject.</span></li>
      <li><strong>2. Review reports</strong><span>The headteacher selects results and enters remarks.</span></li>
      <li><strong>3. Publish reports</strong><span>An owner or administrator publishes each reviewed report.</span></li>
      <li><strong>4. Download</strong><span>The class ZIP needs a published report for every student on the selected roster.</span></li>
    </ol>
    <p>The headteacher enters the class teacher remark supplied to them. The overall score is the equal-weight average of selected subject percentages. Transfers during the term require reconciliation and are not yet supported.</p>
    {error && <p role="alert" className="setup-error">{error}</p>}
    {message && <p role="status" className="academic-success">{message}</p>}
    <details>
      <summary>Download class report cards</summary>
      <p>Downloads the latest published report for every enrollment in this examination&apos;s class on the selected date. The export fails if any report is missing; it does not create or publish reports.</p>
      <label>Class roster date <input type="date" value={rosterDate} onChange={(event) => setRosterDate(event.target.value)} /></label>
      <button type="button" disabled={busy || !rosterDate} onClick={() => void run(
        () => downloadClassReports(authFetch, tenantId, detail.term, detail.school_class, rosterDate),
      )}>Download class reports ZIP</button>
    </details>
    {role === "headteacher" && <details>
      <summary>Review a report card</summary>
      <form className="academic-form" onSubmit={prepare}>
        <label>Report student <select name="enrollment" required defaultValue=""><option value="">Select student</option>
          {detail.students.map((student) => <option key={student.enrollment} value={student.enrollment}>{student.name} · {student.admission_number}</option>)}
        </select></label>
        <fieldset><legend>Report subject results</legend>
          {eligible.length === 0 && <p>No published examination results are available for this class and term.</p>}
          {eligible.map((exam) => <label key={exam.id}><input type="checkbox" name="examinations" value={exam.id} />
            {exam.name} · {subjects.find((subject) => subject.id === exam.subject)?.name ?? exam.subject} · v{exam.published_version}</label>)}
        </fieldset>
        <label>Class teacher remark <textarea name="class_teacher_remark" required maxLength={1000} /></label>
        <label>Headteacher remark <textarea name="headteacher_remark" required maxLength={1000} /></label>
        <label>Revision reason (required for a later version) <input name="reason" maxLength={500} /></label>
        <button type="submit" disabled={busy || !eligible.length}>Review report</button>
      </form>
    </details>}
    {role === "headteacher" && <ReportCardBatch key={detail.id} authFetch={authFetch} tenantId={tenantId} detail={detail}
      exams={exams} subjects={subjects} onReviewed={refresh} />}
    <button type="button" disabled={busy} onClick={() => void run(refresh)}>Load report cards</button>
    <p className="fin-note">The report list includes accessible reports across the school. A reviewed report must be published before it is included in a class ZIP.</p>
    {loaded && reports.length === 0 && <p>No report cards yet.</p>}
    {role === "headteacher" && reports.some((report) => report.status === "reviewed") &&
      <label>Reason to withdraw a review <input value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} /></label>}
    <ul>{reports.map((report) => <li key={report.id}>
      {report.snapshot.student} · {report.snapshot.year} · {report.snapshot.term} · v{report.version} · {report.status}
      <details>
        <summary>Inspect {report.snapshot.student} v{report.version}</summary>
        <p>{report.snapshot.admission_number} · {report.snapshot.class} {report.snapshot.section} · {report.snapshot.campus ?? "No campus assigned"}</p>
        <ul>{report.snapshot.results.map((item) => <li key={item.publication_id}>
          {item.result.subject} · {item.result.examination} · {item.result.score}% · {item.result.grade} · {item.result.description} · result version {item.version}
        </li>)}</ul>
        <p>Attendance: {Object.entries(report.snapshot.attendance).map(([state, count]) => `${state}: ${count}`).join(" · ")}</p>
        <p>Overall: {report.snapshot.overall_average}% · {report.snapshot.overall_method}</p>
        <p>Class teacher remark: {report.snapshot.class_teacher_remark}</p>
        <p>Headteacher remark: {report.snapshot.headteacher_remark}</p>
      </details>
      {report.status === "reviewed" && (role === "owner" || role === "school_admin") &&
        <button type="button" disabled={busy} onClick={() => void run(async () => {
          await changeReport(authFetch, tenantId, `${report.id}/publish/`, {}); await refresh();
        }, "Report published.")}>Publish {report.snapshot.student} v{report.version}</button>}
      {report.status === "reviewed" && role === "headteacher" &&
        <button type="button" disabled={busy || !reason.trim()} onClick={() => void run(async () => {
          await changeReport(authFetch, tenantId, `${report.id}/withdraw/`, { reason }); setReason(""); await refresh();
        }, "Review withdrawn.")}>Withdraw {report.snapshot.student} v{report.version}</button>}
      {report.status === "published" && <button type="button" disabled={busy} onClick={() => void run(
        () => downloadReport(authFetch, tenantId, report.id, report.version),
      )}>Download {report.snapshot.student} v{report.version}</button>}
    </li>)}</ul>
  </section>;
}

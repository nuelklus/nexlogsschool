"use client";

import { FormEvent, useState } from "react";
import { requestError, type AuthFetch } from "../school/student-api";
import type { ExamDetail, Examination } from "./examination-api";
import type { Subject } from "./academics-api";

type Props = { authFetch: AuthFetch; tenantId: string; detail: ExamDetail; exams: Examination[]; subjects: Subject[] };
type Result = { publication_id: string; version: number; result: { subject: string; score: string; grade: string; description: string } };
type Preview = { student: string; admission_number: string; attendance: Record<string, number>; results: Result[] };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
function isPreview(value: unknown): value is Preview {
  return record(value) && typeof value.student === "string" && typeof value.admission_number === "string"
    && record(value.attendance) && Object.values(value.attendance).every((item) => typeof item === "number")
    && Array.isArray(value.results) && value.results.every((item: unknown) => record(item)
      && typeof item.publication_id === "string" && typeof item.version === "number" && record(item.result)
      && ["subject", "score", "grade", "description"].every((key) => typeof (item.result as Record<string, unknown>)[key] === "string"));
}

export default function ReportCardPreview({ authFetch, tenantId, detail, exams, subjects }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const eligible = exams.filter((exam) => exam.term === detail.term && exam.school_class === detail.school_class && exam.published_version > 0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = new FormData(form);
    const format = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "pdf" ? "pdf" : "json";
    setError(""); setPreview(null); setBusy(true);
    try {
      const response = await authFetch(`/tenants/${tenantId}/academics/report-card-preview/`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term: detail.term, enrollment: input.get("enrollment"), examinations: input.getAll("examinations"), format }),
      });
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null);
        throw requestError(response, payload);
      }
      if (format === "pdf") {
        if (!response.headers.get("content-type")?.includes("application/pdf")) throw new Error("Unexpected report preview response.");
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url; link.download = "report-card-preview.pdf"; link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        const data: unknown = await response.json();
        if (!isPreview(data)) throw new Error("Unexpected report preview response.");
        setPreview(data);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not generate the preview.");
    } finally { setBusy(false); }
  }

  return <details>
    <summary>Preview selected published results</summary>
    <p>This unpublished preview is not an official report card. It includes only selected results and attendance for this enrollment; it is not saved or shared with parents.</p>
    {error && <p role="alert" className="setup-error">{error}</p>}
    <form className="academic-form" onSubmit={submit}>
      <label>Student <select name="enrollment" required defaultValue=""><option value="">Select student</option>
        {detail.students.map((student) => <option key={student.enrollment} value={student.enrollment}>{student.name} · {student.admission_number}</option>)}
      </select></label>
      <fieldset><legend>Published examinations (select one per subject)</legend>
        {eligible.length === 0 && <p>No published results are available for this class and term.</p>}
        {eligible.map((exam) => <label key={exam.id}><input type="checkbox" name="examinations" value={exam.id} /> {exam.name} · {subjects.find((subject) => subject.id === exam.subject)?.name ?? exam.subject} · version {exam.published_version}</label>)}
      </fieldset>
      <button disabled={busy || !eligible.length} type="submit" value="json">Show preview</button>
      <button disabled={busy || !eligible.length} type="submit" value="pdf">Download preview PDF</button>
    </form>
    {preview && <div>
      <h4>Unpublished preview: {preview.student} · {preview.admission_number}</h4>
      <ul>{preview.results.map(({ publication_id, result }) => <li key={publication_id}>{result.subject}: {result.score}% · {result.grade} · {result.description}</li>)}</ul>
      <p>Recorded attendance: {Object.entries(preview.attendance).map(([name, count]) => `${name}: ${count}`).join(" · ")}</p>
    </div>}
  </details>;
}

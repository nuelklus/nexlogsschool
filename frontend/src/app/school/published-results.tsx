"use client";

import { useEffect, useState } from "react";
import { AuthFetch, requestError } from "./student-api";

type PublishedResult = {
  id: string;
  result: { student_name: string; admission_number: string; year: string; term: string; class: string;
    examination: string; subject: string; score: string; grade: string; description: string;
    marks?: { component: string; status: string; score: string | null; remarks: string }[] };
};
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
function isResult(value: unknown): value is PublishedResult {
  if (!isRecord(value) || typeof value.id !== "string" || !isRecord(value.result)) return false;
  const result = value.result;
  return ["student_name", "admission_number", "year", "term", "class", "examination",
    "subject", "score", "grade", "description"].every((key) => typeof result[key] === "string")
    && (result.marks === undefined || (Array.isArray(result.marks) && result.marks.every((mark: unknown) =>
      isRecord(mark) && typeof mark.component === "string" && typeof mark.status === "string"
      && (mark.score === null || typeof mark.score === "string") && typeof mark.remarks === "string")));
}

export default function PublishedResults({ tenantId, authFetch }: { tenantId: string; authFetch: AuthFetch }) {
  const [results, setResults] = useState<PublishedResult[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    async function load() {
      const response = await authFetch(`/tenants/${tenantId}/academics/published-results/`);
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw requestError(response, payload);
      if (!Array.isArray(payload) || !payload.every(isResult)) throw new Error("Unexpected published results response.");
      return payload;
    }
    load().then((items) => { if (active) setResults(items); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "Results could not be loaded."); });
    return () => { active = false; };
  }, [authFetch, tenantId]);
  return <section aria-label="Published academic results">
    <h2>Published academic results</h2>
    {error ? <p className="setup-error" role="alert">{error}</p> : results === null
      ? <p role="status">Loading results…</p> : results.length === 0
        ? <p>No results have been published yet.</p>
        : <ul className="school-list">{results.map(({ id, result }) => <li key={id}><div>
          <strong>{result.student_name} · {result.subject}: {result.score}% ({result.grade})</strong>
          <span>{result.admission_number} · {result.class} · {result.year} · {result.term} · {result.examination}</span>
          <span>{result.description}</span>
          {result.marks?.map((mark) => <span key={mark.component}>{mark.component}: {mark.score ?? mark.status}{mark.remarks ? ` · ${mark.remarks}` : ""}</span>)}
        </div></li>)}</ul>}
  </section>;
}

"use client";

import { useState } from "react";
import type { AuthFetch } from "./student-api";
import { downloadReport, listReportCards, type ReportCard } from "../academics/report-card-api";

export default function PublishedReportCards({ tenantId, authFetch }: { tenantId: string; authFetch: AuthFetch }) {
  const [reports, setReports] = useState<ReportCard[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load report cards."); }
    finally { setBusy(false); }
  }
  return <section aria-label="Published report cards">
    <h2>Published report cards</h2>
    {error && <p className="setup-error" role="alert">{error}</p>}
    <button type="button" disabled={busy} onClick={() => void run(async () => setReports(await listReportCards(authFetch, tenantId)))}>Load report cards</button>
    {reports?.length === 0 && <p>No report cards have been published yet.</p>}
    {reports && <ul className="school-list">{reports.map((report) => <li key={report.id}>
      {report.snapshot.student} · {report.snapshot.year} · {report.snapshot.term} · version {report.version}
      <button type="button" disabled={busy} onClick={() => void run(
        () => downloadReport(authFetch, tenantId, report.id, report.version),
      )}>Download report card v{report.version}</button>
    </li>)}</ul>}
  </section>;
}

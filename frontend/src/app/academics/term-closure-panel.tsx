"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import type { AuthFetch } from "../school/student-api";
import { requestError } from "../school/student-api";

export type ClosureItem = {
  id: string | null;
  category: string;
  code: string;
  title: string;
  description: string;
  required: boolean;
  status: "complete" | "incomplete" | "not_applicable" | "exception_approved";
  outstanding_count: number;
  evidence_summary: string;
  details: { classes?: string[]; students?: string[] };
};

export type ClosureOverview = {
  term_name: string;
  academic_year_name: string;
  start_date: string;
  end_date: string;
  today: string;
  status: string;
  is_completed: boolean;
  progress: { done: number; required: number };
  closure: {
    academic_approved_by: string;
    academic_approved_at: string | null;
    final_approved_by: string;
    final_approved_at: string | null;
  } | null;
  permissions: { initialize: boolean; academic_approval: boolean; final_authorization: boolean; reopen: boolean };
  items: ClosureItem[];
  exceptions: {
    id: string; item_code: string; item_title: string; reason: string; status: string;
    requested_by: string; approved_by: string; decision_comments: string;
  }[];
  finance: {
    outstanding_invoice_count: number; outstanding_total: string; students_with_balances: number;
    draft_invoice_count: number; draft_invoice_total: string; invoiced_total: string; collected_total: string;
    collections_by_method: { method: string; total: string }[];
  } | null;
  history: { id: string; stage: string; decision: string; user: string; comments: string; previous_status: string; new_status: string; timestamp: string }[];
};

const CATEGORIES: Record<string, string> = {
  attendance: "Attendance", examinations: "Examinations", reporting: "Reporting", progression: "Academic progression", finance: "Finance",
};
const STATUS_LABEL: Record<string, string> = {
  not_started: "Not started", in_progress: "In progress", submitted_for_review: "Submitted for headteacher review",
  returned_for_correction: "Returned for correction", academic_approved: "Academically approved",
  pending_final_approval: "Awaiting final authorization", completed: "Completed", rejected: "Rejected",
  cancelled: "Cancelled", reopened: "Reopened",
};
const ITEM_LABEL: Record<string, string> = {
  complete: "Done", incomplete: "Outstanding", not_applicable: "Not applicable", exception_approved: "Exception approved",
};
const MODULE_TAB: Record<string, string> = { attendance: "attendance", examinations: "exams", progression: "promotion" };
const EDITABLE = ["in_progress", "returned_for_correction", "reopened"];

const money = (value: string) => `GHS ${Number(value).toLocaleString("en-GH", { minimumFractionDigits: 2 })}`;

// The Term Closure workspace: checklist from live data, headteacher review, then owner/admin final authorization.
export default function TermClosurePanel({
  authFetch,
  tenantId,
  termId,
  onBack,
  onChanged,
  onOpenTab,
}: {
  authFetch: AuthFetch;
  tenantId: string;
  termId: string;
  onBack: () => void;
  onChanged: () => Promise<void>;
  onOpenTab: (tab: string) => void;
}) {
  const [data, setData] = useState<ClosureOverview | null>(null);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [comments, setComments] = useState("");
  const [exceptionFor, setExceptionFor] = useState("");
  const [reason, setReason] = useState("");
  const base = `/tenants/${tenantId}/academics/terms/${termId}/closure/`;

  const call = useCallback(async (path: string, body?: unknown): Promise<ClosureOverview> => {
    const response = await authFetch(path, body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw requestError(response, payload);
    return payload as ClosureOverview;
  }, [authFetch]);

  useEffect(() => {
    let current = true;
    call(base)
      .then((overview) => current && setData(overview))
      .catch((caught) => {
        if (!current) return;
        setFailed(true);
        setMessage(caught instanceof Error ? caught.message : "The term closure could not be loaded.");
      });
    return () => { current = false; };
  }, [base, call]);

  async function act(action: string, body: Record<string, unknown>, success: string) {
    setBusy(true);
    setMessage("");
    try {
      setData(await call(`${base}${action}/`, body));
      setFailed(false);
      setMessage(success);
      setComments("");
      setExceptionFor("");
      setReason("");
      await onChanged();
    } catch (caught) {
      setFailed(true);
      setMessage(caught instanceof Error ? caught.message : "The action could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <section className="academic-card" aria-label="Term closure">
        <button type="button" className="link-button" onClick={onBack}>← Back to terms</button>
        {message ? <p className="setup-error" role="alert">{message}</p> : <p role="status">Loading term closure…</p>}
      </section>
    );
  }

  const { permissions: can, status } = data;
  const started = status !== "not_started";
  const editable = EDITABLE.includes(status);
  const groups = Object.keys(CATEGORIES).map((key) => ({ key, items: data.items.filter((item) => item.category === key) })).filter((g) => g.items.length > 0);
  const pending = data.exceptions.filter((item) => item.status === "pending");
  const needsComment = (extra = "") => (
    <label className="academic-field-wide">Comments {extra}
      <input value={comments} onChange={(event) => setComments(event.target.value)} maxLength={500} />
    </label>
  );

  return (
    <section className="academic-card" aria-label="Term closure">
      <button type="button" className="link-button" onClick={onBack}>← Back to terms</button>
      <h2>{data.term_name} closure · {data.academic_year_name}</h2>
      <p className="fin-note">
        {data.start_date} – {data.end_date} · Status: <strong>{STATUS_LABEL[status] ?? status}</strong> ·
        Required checklist: {data.progress.done} of {data.progress.required} done
        {data.today < data.end_date && !data.is_completed ? ` · Final closure opens on ${data.end_date}` : ""}
      </p>
      {message && <p className={failed ? "setup-error" : "academic-success"} role={failed ? "alert" : "status"}>{message}</p>}

      {!started && (
        can.initialize
          ? <button type="button" className="setup-submit" disabled={busy} onClick={() => void act("initialize", {}, "Term closure started.")}>Start term closure</button>
          : <p className="academic-empty">The closure has not been started. A headteacher, school administrator or owner can start it.</p>
      )}

      {groups.map((group) => (
        <div key={group.key}>
          <h3>{CATEGORIES[group.key]}</h3>
          <ul className="academic-records">
            {group.items.map((item) => (
              <li key={item.code}>
                <strong>{item.title}{item.required ? "" : " (optional)"} — {ITEM_LABEL[item.status]}</strong>
                <span>
                  {item.evidence_summary || item.description}
                  {item.outstanding_count > 0 && item.status !== "not_applicable" ? ` Outstanding: ${item.outstanding_count}.` : ""}
                  {item.details.classes && item.details.classes.length > 0 ? ` Classes: ${item.details.classes.join(", ")}.` : ""}
                  {item.details.students && item.details.students.length > 0 ? ` Students: ${item.details.students.join(", ")}.` : ""}
                </span>
                {item.status !== "not_applicable" && (MODULE_TAB[item.category] || item.category === "finance") && (
                  item.category === "finance"
                    ? <Link className="link-button" href="/finance">Open Finance</Link>
                    : <button type="button" className="link-button" onClick={() => onOpenTab(MODULE_TAB[item.category])}>Open module</button>
                )}
                {started && editable && can.initialize && item.required && item.status === "incomplete" && (
                  exceptionFor === item.code ? (
                    <span className="academic-form">
                      <input aria-label="Exception reason" placeholder="Why can this be waived?" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} />
                      <button type="button" className="setup-submit" disabled={busy || !reason.trim()} onClick={() => void act("exceptions", { item: item.code, reason }, "Exception requested for the headteacher.")}>Send request</button>
                    </span>
                  ) : <button type="button" className="link-button" onClick={() => setExceptionFor(item.code)}>Request exception</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}

      {data.exceptions.length > 0 && (
        <div>
          <h3>Exceptions</h3>
          <ul className="academic-records">
            {data.exceptions.map((item) => (
              <li key={item.id}>
                <strong>{item.item_title} — {item.status}</strong>
                <span>{item.reason} · requested by {item.requested_by || "—"}{item.approved_by ? ` · decided by ${item.approved_by}` : ""}{item.decision_comments ? ` · ${item.decision_comments}` : ""}</span>
                {item.status === "pending" && can.academic_approval && (
                  <span>
                    <button type="button" className="link-button" disabled={busy} onClick={() => void act(`exceptions/${item.id}/decision`, { decision: "approve", comments }, "Exception approved.")}>Approve</button>
                    <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act(`exceptions/${item.id}/decision`, { decision: "reject", comments }, "Exception rejected.")}>Reject (needs a comment)</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.finance && <>
      <h3>Financial summary</h3>
      <p className="fin-note">Balances never block closure; unpaid amounts carry forward to the student&apos;s next invoice.</p>
      <table className="fin-table">
        <tbody>
          <tr><td>Outstanding invoices</td><td className="num">{data.finance.outstanding_invoice_count} ({data.finance.students_with_balances} students)</td></tr>
          <tr><td>Total outstanding</td><td className="num">{money(data.finance.outstanding_total)}</td></tr>
          <tr><td>Draft invoices</td><td className="num">{data.finance.draft_invoice_count} · {money(data.finance.draft_invoice_total)}</td></tr>
          <tr><td>Invoiced</td><td className="num">{money(data.finance.invoiced_total)}</td></tr>
          <tr><td>Collected</td><td className="num">{money(data.finance.collected_total)}</td></tr>
          {data.finance.collections_by_method.map((row) => (
            <tr key={row.method}><td>&nbsp;&nbsp;{row.method}</td><td className="num">{money(row.total)}</td></tr>
          ))}
        </tbody>
      </table>
      </>}

      {started && (
        <div className="academic-form">
          <h3>Review and approval</h3>
          {data.closure?.academic_approved_by && <p className="fin-note">Academic approval by {data.closure.academic_approved_by}.</p>}
          {data.closure?.final_approved_by && <p className="fin-note">Final authorization by {data.closure.final_approved_by}.</p>}
          {needsComment("(required to return, reject or reopen)")}
          <div className="enrol-actions">
            {can.initialize && editable && (
              <button type="button" className="link-button" disabled={busy} onClick={() => void act("refresh", {}, "Checklist recalculated.")}>Recalculate checklist</button>
            )}
            {can.initialize && editable && (
              <button type="button" className="setup-submit" disabled={busy} onClick={() => void act("submit", { comments }, "Submitted for headteacher review.")}>Submit for headteacher review</button>
            )}
            {can.academic_approval && status === "submitted_for_review" && (
              <>
                <button type="button" className="setup-submit" disabled={busy} onClick={() => void act("academic-approval", { decision: "approve", comments }, "Academic completion approved.")}>Approve academic completion</button>
                <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act("academic-approval", { decision: "return", comments }, "Returned for correction.")}>Return for correction</button>
              </>
            )}
            {can.final_authorization && status === "pending_final_approval" && (
              <>
                <button type="button" className="setup-submit" disabled={busy} onClick={() => void act("final-authorization", { decision: "approve", comments }, "Term closed.")}>Authorize final closure</button>
                <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act("final-authorization", { decision: "return", comments }, "Returned for correction.")}>Return for correction</button>
                <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act("final-authorization", { decision: "reject", comments }, "Closure rejected.")}>Reject closure</button>
              </>
            )}
            {can.reopen && status === "completed" && (
              <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act("reopen", { reason: comments }, "Term reopened. It must be approved again.")}>Reopen term</button>
            )}
            {can.reopen && !["completed", "cancelled", "rejected"].includes(status) && (
              <button type="button" className="link-button" disabled={busy || !comments.trim()} onClick={() => void act("cancel", { reason: comments }, "Closure cancelled.")}>Cancel closure</button>
            )}
          </div>
          {pending.length > 0 && <p className="fin-note">{pending.length} exception request(s) waiting for the headteacher.</p>}
        </div>
      )}

      <h3>Approval history</h3>
      {data.history.length === 0 ? <p className="academic-empty">No actions recorded yet.</p> : (
        <ul className="academic-records">
          {data.history.map((row) => (
            <li key={row.id}>
              <strong>{row.stage} · {row.decision} — {row.user || "—"}</strong>
              <span>{new Date(row.timestamp).toLocaleString()} · {row.previous_status || "—"} → {row.new_status}{row.comments ? ` · ${row.comments}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

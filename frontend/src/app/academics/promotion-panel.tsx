"use client";

import { useState } from "react";

import type { AuthFetch } from "../school/student-api";
import { requestError } from "../school/student-api";
import type { AcademicYear, SchoolClass } from "./academics-api";

type Row = {
  enrollment: string;
  student_name: string;
  admission_number: string;
  from_class: string;
  suggested_action: string;
  suggested_class: string | null;
  already_enrolled: boolean;
};
type Preview = { target_classes: { id: string; name: string }[]; rows: Row[] };
type Decision = { action: string; target_class: string };

const ACTIONS = [
  { value: "", label: "— Choose —" },
  { value: "promote", label: "Promote" },
  { value: "repeat", label: "Repeat class" },
  { value: "graduate", label: "Graduate" },
  { value: "leave", label: "Leave as is" },
];

async function call<T>(authFetch: AuthFetch, path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(path, init);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload as T;
}

export default function PromotionPanel({
  authFetch,
  tenantId,
  years,
  classes,
  onApplied,
}: {
  authFetch: AuthFetch;
  tenantId: string;
  years: AcademicYear[];
  classes: SchoolClass[];
  onApplied: () => Promise<void>;
}) {
  const current = years.find((year) => year.is_current) ?? years[0];
  const [from, setFrom] = useState(current?.id ?? "");
  const [to, setTo] = useState(years.find((year) => year.id !== current?.id)?.id ?? "");
  const [sourceClass, setSourceClass] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [makeCurrent, setMakeCurrent] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const base = `/tenants/${tenantId}/academics/promotion/`;

  const fail = (caught: unknown) => {
    setFailed(true);
    setMessage(caught instanceof Error ? caught.message : "Something went wrong. Try again.");
  };

  async function loadPreview() {
    setBusy(true);
    setMessage("");
    try {
      const params = new URLSearchParams({ from_year: from, to_year: to, source_class: sourceClass });
      const data = await call<Preview>(authFetch, `${base}?${params}`);
      setPreview(data);
      setDecisions(Object.fromEntries(data.rows.map((row) => [
        row.enrollment,
        { action: row.suggested_action, target_class: row.suggested_class ?? "" },
      ])));
      setFailed(false);
    } catch (caught) {
      setPreview(null);
      fail(caught);
    } finally {
      setBusy(false);
    }
  }

  function bulk(action: string) {
    if (!preview) return;
    setDecisions(Object.fromEntries(preview.rows.map((row) => [
      row.enrollment,
      row.already_enrolled ? decisions[row.enrollment] : {
        action, target_class: decisions[row.enrollment]?.action === action ? decisions[row.enrollment].target_class : "",
      },
    ])));
  }

  const pending = preview?.rows.filter((row) => !row.already_enrolled) ?? [];
  const chosen = pending.filter((row) => decisions[row.enrollment]?.action);
  const needsClass = chosen.filter((row) => decisions[row.enrollment].action === "promote" && !decisions[row.enrollment].target_class);

  async function apply() {
    if (!preview || chosen.length === 0 || needsClass.length > 0) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await call<Record<string, number>>(authFetch, base, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_year: from,
          to_year: to,
          make_current: makeCurrent,
          decisions: chosen.map((row) => ({ enrollment: row.enrollment, ...decisions[row.enrollment] })),
        }),
      });
      setFailed(false);
      setMessage(`Done: ${result.promote} promoted, ${result.repeat} repeating, ${result.graduate} graduated, ${result.leave} left as is.`);
      setPreview(null);
      try {
        await onApplied();
      } catch {
        setFailed(true);
        setMessage("Promotion was applied, but the workspace could not be refreshed. Reload before continuing.");
      }
    } catch (caught) {
      fail(caught);
    } finally {
      setBusy(false);
    }
  }

  const yearSelect = (value: string, onChange: (id: string) => void, label: string, resetClass = false) => (
    <label>{label}
      <select value={value} disabled={busy} onChange={(event) => { onChange(event.target.value); setPreview(null); setMessage(""); if (resetClass) setSourceClass(""); }}>
        <option value="" disabled>Select year</option>
        {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
      </select>
    </label>
  );
  const selectedClass = classes.find((item) => item.id === sourceClass);

  return (
    <section className="academic-card" aria-label="Promote students">
      <h2>Promote students to the next year</h2>
      <p className="fin-note">
        Create the next year&apos;s classes first. Where a destination is unambiguous, a class is suggested; review each row before confirming.
        Choose one source class at a time. Set the school&apos;s terminal level in Class levels to suggest graduation. Last year&apos;s class records stay as history.
      </p>
      <div className="academic-form">
        {yearSelect(from, setFrom, "Promote from", true)}
        {yearSelect(to, setTo, "Promote into")}
        <label>Source class
          <select value={sourceClass} disabled={busy || !from} onChange={(event) => { setSourceClass(event.target.value); setPreview(null); setMessage(""); }}>
            <option value="">Select class</option>
            {classes.filter((item) => item.academic_year === from).map((item) =>
              <option key={item.id} value={item.id}>{item.name}{item.section ? ` ${item.section}` : ""}{item.is_active === false ? " (archived)" : ""}</option>)}
          </select>
        </label>
        <button type="button" className="setup-submit" disabled={busy || !from || !to || !sourceClass} onClick={() => void loadPreview()}>Load students</button>
      </div>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      {preview && (
        preview.rows.length === 0 ? <p className="academic-empty">No active students are enrolled in the selected class.</p> : (
          <>
            <p>Showing students in {selectedClass?.name}{selectedClass?.section ? ` ${selectedClass.section}` : ""}. Actions below apply only to this class.</p>
            <p>
              Set all to:{" "}
              {ACTIONS.slice(1).map((item) => (
                <button type="button" disabled={busy} key={item.value} className="link-button" onClick={() => bulk(item.value)}>{item.label}</button>
              )).reduce<React.ReactNode[]>((acc, node, index) => (index ? [...acc, " · ", node] : [node]), [])}
            </p>
            <table className="fin-table">
              <thead><tr><th>Student</th><th>Now in</th><th>Action</th><th>Next class</th></tr></thead>
              <tbody>
                {preview.rows.map((row) => {
                  const decision = decisions[row.enrollment] ?? { action: "", target_class: "" };
                  const set = (patch: Partial<Decision>) => setDecisions({ ...decisions, [row.enrollment]: { ...decision, ...patch } });
                  return (
                    <tr key={row.enrollment}>
                      <td>{row.student_name}<small>{row.admission_number}</small></td>
                      <td>{row.from_class}</td>
                      {row.already_enrolled ? <td colSpan={2}>Already placed in the new year</td> : (
                        <>
                          <td>
                            <select disabled={busy} aria-label={`Action for ${row.student_name}`} value={decision.action} onChange={(event) => set({ action: event.target.value, target_class: event.target.value === decision.action ? decision.target_class : "" })}>
                              {ACTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                            </select>
                          </td>
                          <td>
                            {(decision.action === "promote" || decision.action === "repeat") && (
                              <select disabled={busy} aria-label={`Next class for ${row.student_name}`} value={decision.target_class} onChange={(event) => set({ target_class: event.target.value })}>
                                <option value="">{decision.action === "repeat" ? "Same class" : "Choose class"}</option>
                                {preview.target_classes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                              </select>
                            )}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <label className="fin-check"><input type="checkbox" disabled={busy} checked={makeCurrent} onChange={(event) => setMakeCurrent(event.target.checked)} /> Make the new year the current academic year for the whole school (updates placed students&apos; class for billing)</label>
            <p className="fin-note">Leave this unchecked while placing classes in advance. Changing the current year affects the whole school, not only this class.</p>
            {years.find((year) => year.id === to)?.is_current && <p className="fin-note">The destination year is already current, so applying placements updates students&apos; billing classes even if the box is unchecked.</p>}
            {needsClass.length > 0 && <p className="setup-error" role="alert">Choose a next class for {needsClass.length} promoted student(s).</p>}
            <div className="enrol-actions">
              <button type="button" className="setup-submit" disabled={busy || chosen.length === 0 || needsClass.length > 0} onClick={() => void apply()}>
                {busy ? "Applying…" : `Apply to ${chosen.length} student${chosen.length === 1 ? "" : "s"}`}
              </button>
            </div>
          </>
        )
      )}
    </section>
  );
}

"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { NoTermsHint } from "./no-terms-hint";
import { useTermOptions } from "./use-term-options";
import { Student, listStudents } from "../school/student-api";
import { FeeCategory, Invoice, Preview, financeRequest, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, useFinanceData } from "./finance-shell";

type Year = { id: string; name: string; is_current: boolean };
type BillingClass = { id: string; name: string; section: string; is_active: boolean };

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

export function useStudents(ctx: FinanceContext) {
  const [students, setStudents] = useState<Student[]>([]);
  const { authFetch, tenantId, onFailure } = ctx;
  useEffect(() => {
    let current = true;
    listStudents(authFetch, tenantId)
      .then((list) => current && setStudents(list))
      .catch((error: unknown) => current && onFailure(error));
    return () => {
      current = false;
    };
  }, [authFetch, tenantId, onFailure]);
  return students;
}

function GenerateForm({ ctx, years }: { ctx: FinanceContext; years: Year[] }) {
  const students = useStudents(ctx);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draftId, setDraftId] = useState("");
  const current = years.find((year) => year.is_current) ?? years[0];
  const [yearId, setYearId] = useState(current?.id ?? "");
  const terms = useTermOptions(ctx, yearId);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setBusy(true);
    setMessage("");
    setDraftId("");
    try {
      const draft = await financeRequest<Invoice>(ctx.authFetch, ctx.tenantId, "invoices/", {
        method: "POST",
        body: {
          student: text(values, "student"),
          academic_year: text(values, "academic_year"),
          period_label: text(values, "period_label"),
          due_date: text(values, "due_date") || undefined,
        },
      });
      setFailed(false);
      setMessage(`Draft created for ${draft.student_name}: ${formatMoney(draft.total)}. Review it, then issue it.`);
      setDraftId(draft.id);
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="enrol-card" onSubmit={submit} aria-label="Create invoice">
      <div className="enrol-card-head"><div><p className="eyebrow">NEW INVOICE</p><h2>Bill a student</h2></div></div>
      <p className="fin-note">The draft is built from the current fee schedules and approved scholarships. Students see it only after you issue it.</p>
      <div className="enrol-grid">
        <label className="enrol-wide">Student
          <select name="student" required defaultValue="">
            <option value="" disabled>Select student</option>
            {students.filter((s) => s.status === "active").map((s) => (
              <option key={s.id} value={s.id}>{s.first_name} {s.last_name} · {s.admission_number}</option>
            ))}
          </select>
        </label>
        <label>Academic year
          <select name="academic_year" required value={yearId} onChange={(event) => setYearId(event.target.value)}>
            {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
          </select>
        </label>
        <label>Term
          <select name="period_label" required defaultValue="" key={yearId}>
            <option value="" disabled>Select term</option>
            {terms.map((term) => <option key={term} value={term}>{term}</option>)}
          </select>
        </label>
        <NoTermsHint ctx={ctx} yearId={yearId} />
        <label>Due date (optional)<input name="due_date" type="date" /></label>
      </div>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      {draftId && <p><Link href={`/finance/invoices/${draftId}`}>Review this invoice</Link></p>}
      <div className="enrol-actions"><button className="setup-submit" type="submit" disabled={busy}>{busy ? "Creating…" : "Create draft invoice"}</button></div>
    </form>
  );
}

function BulkBilling({ ctx, years }: { ctx: FinanceContext; years: Year[] }) {
  const current = years.find((year) => year.is_current) ?? years[0];
  const categories = useFinanceData<FeeCategory[]>(ctx, "fee-categories/");
  const [form, setForm] = useState({ year: current?.id ?? "", term: "", schoolClass: "", due: "" });
  const terms = useTermOptions(ctx, form.year);
  const [classState, setClassState] = useState<{ year: string; data: BillingClass[] | null; error: string }>({ year: "", data: null, error: "" });
  const [classAttempt, setClassAttempt] = useState(0);
  const [supplementary, setSupplementary] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const { authFetch, tenantId, onFailure } = ctx;

  useEffect(() => {
    let active = true;
    financeRequest<unknown>(authFetch, tenantId, `billing-classes/?academic_year=${encodeURIComponent(form.year)}`)
      .then((result) => {
        if (!Array.isArray(result) || !result.every((item) =>
          item && typeof item === "object" && typeof item.id === "string" && typeof item.name === "string"
          && typeof item.section === "string" && typeof item.is_active === "boolean")) {
          throw new Error("The server returned an unexpected class list.");
        }
        if (active) setClassState({ year: form.year, data: result, error: "" });
      })
      .catch((error: unknown) => {
        if (active) {
          onFailure(error);
          setClassState({ year: form.year, data: null, error: errorText(error) });
        }
      });
    return () => { active = false; };
  }, [authFetch, tenantId, onFailure, form.year, classAttempt]);

  const classes = classState.year === form.year ? classState.data : null;
  const classError = classState.year === form.year ? classState.error : "";

  const body = () => ({
    academic_year: form.year,
    period_label: form.term,
    school_class: form.schoolClass,
    due_date: form.due || undefined,
    supplementary: supplementary || undefined,
    categories: supplementary ? chosen : undefined,
  });
  const ready = Boolean(form.year && form.term && classes?.some((item) => item.id === form.schoolClass) && (!supplementary || chosen.length > 0));

  function changeForm(next: typeof form) {
    setForm(next);
    setPreview(null);
    setMessage("");
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const runPreview = () =>
    run(async () => {
      setPreview(null);
      setPreview(await financeRequest<Preview>(ctx.authFetch, ctx.tenantId, "invoices/preview/", { method: "POST", body: body() }));
    });

  const createDrafts = () =>
    run(async () => {
      type Bulk = { created: number; failed: number; results: { invoice: string | null; student_name: string; error: string | null }[] };
      const result = await financeRequest<Bulk>(ctx.authFetch, ctx.tenantId, "invoices/bulk/", { method: "POST", body: body() });
      setDrafts(result.results.flatMap((r) => (r.invoice ? [r.invoice] : [])));
      const problems = result.results.filter((r) => r.error).map((r) => `${r.student_name}: ${r.error}`);
      setFailed(problems.length > 0);
      setMessage(`${result.created} draft invoice(s) created.${problems.length ? ` Not created: ${problems.join("; ")}` : ""}`);
    });

  const issueDrafts = () =>
    run(async () => {
      if (!window.confirm(`Issue ${drafts.length} invoice(s)? Issued invoices become visible to parents and post to the ledger.`)) return;
      const result = await financeRequest<{ issued: number; failed: number; errors: { error: string }[] }>(
        ctx.authFetch, ctx.tenantId, "invoices/issue-batch/", { method: "POST", body: { invoices: drafts } },
      );
      setFailed(result.failed > 0);
      setMessage(`${result.issued} invoice(s) issued.${result.failed ? ` ${result.failed} failed: ${result.errors.map((e) => e.error).join("; ")}` : ""}`);
      setDrafts([]);
      setPreview(null);
    });

  return (
    <section className="enrol-card" aria-label="Bulk billing">
      <div className="enrol-card-head"><div><p className="eyebrow">BULK BILLING</p><h2>Preview and bill a class</h2></div></div>
      <p className="fin-note">Preview shows exactly what each student would be charged, and which optional fees are left out, before anything is created.</p>
      <p className="fin-note">Only students with an eligible enrollment in the selected class are included. Students without a class enrollment must be enrolled before class billing, or billed individually.</p>
      <fieldset className="enrol-fieldset" disabled={busy || drafts.length > 0}>
      <legend>Billing selection</legend>
      <div className="enrol-grid">
        <label>Academic year
          <select value={form.year} onChange={(event) => changeForm({ ...form, year: event.target.value, term: "", schoolClass: "" })}>
            {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
          </select>
        </label>
        <label>Term
          <select value={form.term} onChange={(event) => changeForm({ ...form, term: event.target.value })}>
            <option value="" disabled>Select term</option>
            {terms.map((term) => <option key={term} value={term}>{term}</option>)}
          </select>
        </label>
        <NoTermsHint ctx={ctx} yearId={form.year} />
        <label>Class
          <select value={form.schoolClass} disabled={!classes} onChange={(event) => changeForm({ ...form, schoolClass: event.target.value })}>
            <option value="" disabled>Select class</option>
            {classes?.map((item) => <option key={item.id} value={item.id}>{item.name}{item.section ? ` · ${item.section}` : ""}{item.is_active ? "" : " (archived)"}</option>)}
          </select>
        </label>
        <label>Due date (optional)<input type="date" value={form.due} onChange={(event) => changeForm({ ...form, due: event.target.value })} /></label>
        <label className="enrol-wide">
          <span><input type="checkbox" checked={supplementary} onChange={(event) => { setSupplementary(event.target.checked); setPreview(null); }} /> Supplementary invoice for services added after the term was billed</span>
        </label>
        {supplementary && (
          <fieldset className="enrol-wide">
            <legend>Bill only these services</legend>
            {(categories.data ?? []).filter((c) => c.is_active).map((c) => (
              <label key={c.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(c.id)}
                  onChange={(event) => { setChosen(event.target.checked ? [...chosen, c.id] : chosen.filter((id) => id !== c.id)); setPreview(null); }}
                /> {c.name}
              </label>
            ))}
          </fieldset>
        )}
      </div>
      </fieldset>
      {classError && <p className="setup-error" role="alert">{classError}</p>}
      {classError && <button className="auth-signout" type="button" onClick={() => { setClassState({ year: "", data: null, error: "" }); setClassAttempt((value) => value + 1); }}>Retry classes</button>}
      {!classes && !classError && <p className="fin-note" role="status">Loading classes…</p>}
      {classes?.length === 0 && <p className="fin-note">No classes are configured for this academic year.</p>}
      <div className="enrol-actions">
        <button type="button" className="setup-submit" disabled={busy || !ready || drafts.length > 0} onClick={() => void runPreview()}>Preview invoices</button>
      </div>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      {preview && (
        <div aria-label="Invoice preview">
          <p className="fin-note">
            {preview.ready} ready · {preview.blocked} blocked · Total {formatMoney(preview.total)}
          </p>
          <table className="fin-table">
            <thead><tr><th>Student</th><th>Charged</th><th>Left out</th><th className="num">Total</th></tr></thead>
            <tbody>
              {preview.students.map((row) => (
                <tr key={row.student}>
                  <td>{row.student_name}<small>{row.admission_number}</small></td>
                  <td>
                    {row.calculation?.fees.map((f) => <div key={f.description}>{f.description}: {formatMoney(f.amount)}</div>)}
                    {row.calculation?.charges.map((f) => <div key={f.description}>{f.description}: {formatMoney(f.amount)}</div>)}
                    {row.calculation?.discounts.map((f) => <div key={f.description}>{f.description}: −{formatMoney(f.amount)}</div>)}
                  </td>
                  <td>
                    {row.calculation?.excluded.map((x) => <div key={x.description + x.code}><strong>{x.description}</strong>: {x.reason}</div>)}
                    {row.issues.map((issue) => <div key={issue} className="setup-error" role="alert">{issue}</div>)}
                    {row.warnings?.map((warning) => <div key={warning} className="fin-note" role="note">{warning}</div>)}
                  </td>
                  <td className="num">{row.totals ? formatMoney(row.totals.total) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="enrol-actions">
            <button type="button" className="setup-submit" disabled={busy || preview.ready === 0 || drafts.length > 0} onClick={() => void createDrafts()}>
              Create {preview.ready} draft invoice(s)
            </button>
            {drafts.length > 0 && (
              <>
                <button type="button" className="setup-submit" disabled={busy} onClick={() => void issueDrafts()}>Issue {drafts.length} draft(s)</button>
                <button type="button" className="auth-signout" disabled={busy} onClick={() => { setDrafts([]); setPreview(null); setMessage("Created drafts remain available under Finance → Invoices."); setFailed(false); }}>Start another preview</button>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function Billing({ ctx }: { ctx: FinanceContext }) {
  const years = useFinanceData<Year[]>(ctx, "academic-years/");

  return (
    <div className="enrol-layout">
      {years.data && years.data.length > 0 ? (
        <>
          <GenerateForm ctx={ctx} years={years.data} />
          <BulkBilling ctx={ctx} years={years.data} />
        </>
      ) : (
        <section className="enrol-card"><p className="roster-empty">{years.loading ? "Loading…" : "Add an academic year and fee schedules under Fees before billing students."}</p></section>
      )}
    </div>
  );
}

export default function BillingPage() {
  return (
    <FinanceShell
      eyebrow="BILLING"
      title="Billing"
      active="/finance/billing"
      description="Create draft invoices from fee schedules, check them, then issue them. Only issued invoices appear to parents and students."
    >
      {(ctx) => <Billing ctx={ctx} />}
    </FinanceShell>
  );
}

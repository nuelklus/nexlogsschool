"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { GRADE_LEVELS, TERMS } from "../grade-levels";
import { Student, listStudents } from "../school/student-api";
import { FeeCategory, Invoice, Preview, financeRequest, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, useFinanceData } from "./finance-shell";

type Year = { id: string; name: string; is_current: boolean };

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
          <select name="academic_year" required defaultValue={current?.id ?? ""} key={current?.id ?? "none"}>
            {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
          </select>
        </label>
        <label>Term
          <select name="period_label" required defaultValue="">
            <option value="" disabled>Select term</option>
            {TERMS.map((term) => <option key={term} value={term}>{term}</option>)}
          </select>
        </label>
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
  const [form, setForm] = useState({ year: current?.id ?? "", term: "", grade: "", due: "" });
  const [supplementary, setSupplementary] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const body = () => ({
    academic_year: form.year,
    period_label: form.term,
    grade_level: form.grade,
    due_date: form.due || undefined,
    supplementary: supplementary || undefined,
    categories: supplementary ? chosen : undefined,
  });
  const ready = Boolean(form.year && form.term && form.grade && (!supplementary || chosen.length > 0));

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
      setDrafts([]);
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
    });

  return (
    <section className="enrol-card" aria-label="Bulk billing">
      <div className="enrol-card-head"><div><p className="eyebrow">BULK BILLING</p><h2>Preview and bill a class</h2></div></div>
      <p className="fin-note">Preview shows exactly what each student would be charged, and which optional fees are left out, before anything is created.</p>
      <div className="enrol-grid">
        <label>Academic year
          <select value={form.year} onChange={(event) => setForm({ ...form, year: event.target.value })}>
            {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
          </select>
        </label>
        <label>Term
          <select value={form.term} onChange={(event) => setForm({ ...form, term: event.target.value })}>
            <option value="" disabled>Select term</option>
            {TERMS.map((term) => <option key={term} value={term}>{term}</option>)}
          </select>
        </label>
        <label>Class
          <select value={form.grade} onChange={(event) => setForm({ ...form, grade: event.target.value })}>
            <option value="" disabled>Select class</option>
            {GRADE_LEVELS.map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
        </label>
        <label>Due date (optional)<input type="date" value={form.due} onChange={(event) => setForm({ ...form, due: event.target.value })} /></label>
        <label className="enrol-wide">
          <span><input type="checkbox" checked={supplementary} onChange={(event) => setSupplementary(event.target.checked)} /> Supplementary invoice for services added after the term was billed</span>
        </label>
        {supplementary && (
          <fieldset className="enrol-wide">
            <legend>Bill only these services</legend>
            {(categories.data ?? []).filter((c) => c.is_active).map((c) => (
              <label key={c.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(c.id)}
                  onChange={(event) => setChosen(event.target.checked ? [...chosen, c.id] : chosen.filter((id) => id !== c.id))}
                /> {c.name}
              </label>
            ))}
          </fieldset>
        )}
      </div>
      <div className="enrol-actions">
        <button type="button" className="setup-submit" disabled={busy || !ready} onClick={() => void runPreview()}>Preview invoices</button>
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
              <button type="button" className="setup-submit" disabled={busy} onClick={() => void issueDrafts()}>Issue {drafts.length} draft(s)</button>
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

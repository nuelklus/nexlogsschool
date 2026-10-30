"use client";

import { FormEvent, useMemo, useState } from "react";

import { TERMS } from "../grade-levels";
import { Adjustment, Enrollment, FeeCategory, FeeSchedule, Usage, financeRequest, formatMoney } from "./finance-api";
import { useStudents } from "./billing-page";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

type Year = { id: string; name: string; is_current: boolean };

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function useStudentOptions(ctx: FinanceContext) {
  return useStudents(ctx).filter((s) => s.status === "active");
}

function Enrollments({ ctx, serviceCategories }: { ctx: FinanceContext; serviceCategories: FeeCategory[] }) {
  const students = useStudentOptions(ctx);
  const { data, error, loading, reload } = useFinanceData<Enrollment[]>(ctx, "service-enrollments/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [history, setHistory] = useState<Enrollment | null>(null);

  async function subscribe(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "service-enrollments/", {
        method: "POST",
        body: {
          student: text(values, "student"),
          category: text(values, "category"),
          start_date: text(values, "start_date"),
          end_date: text(values, "end_date") || undefined,
          notes: text(values, "notes") || undefined,
        },
      });
      setFailed(false);
      setMessage("Subscription saved. It applies to invoices generated from now on; issued invoices are not changed.");
      form.reset();
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function change(enrollment: Enrollment, status: Enrollment["status"]) {
    const reason = window.prompt(`Reason for marking this subscription ${status}?`);
    if (reason === null) return;
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `service-enrollments/${enrollment.id}/`, {
        method: "PATCH",
        body: { status, reason },
      });
      setFailed(false);
      setMessage("");
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function showHistory(enrollment: Enrollment) {
    try {
      setHistory(await financeRequest<Enrollment>(ctx.authFetch, ctx.tenantId, `service-enrollments/${enrollment.id}/`));
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <div className="enrol-layout">
      <form className="enrol-card" onSubmit={subscribe} aria-label="Subscribe student">
        <div className="enrol-card-head"><div><p className="eyebrow">SERVICE ENROLMENT</p><h2>Subscribe a student</h2></div></div>
        <p className="fin-note">Students are billed for optional services only while subscribed.</p>
        {serviceCategories.length === 0 && <p className="roster-empty">Set an optional or individual fee under Fees first.</p>}
        <div className="enrol-grid">
          <label className="enrol-wide">Student
            <select name="student" required defaultValue="">
              <option value="" disabled>Select student</option>
              {students.map((s) => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} · {s.admission_number}</option>)}
            </select>
          </label>
          <label>Service
            <select name="category" required defaultValue="">
              <option value="" disabled>Select service</option>
              {serviceCategories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label>Starts<input name="start_date" type="date" required /></label>
          <label>Ends (optional)<input name="end_date" type="date" /></label>
          <label>Notes (optional)<input name="notes" maxLength={240} /></label>
        </div>
        {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
        <div className="enrol-actions"><button className="setup-submit" type="submit">Save subscription</button></div>
      </form>
      <section className="enrol-card roster" aria-label="Subscriptions">
        <div className="enrol-card-head"><div><p className="eyebrow">SUBSCRIPTIONS</p><h2>Who is subscribed</h2></div></div>
        <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No subscriptions yet." />
        {data && data.length > 0 && (
          <table className="fin-table">
            <thead><tr><th>Student</th><th>Service</th><th>From</th><th>To</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.id}>
                  <td>{e.student_name}<small>{e.admission_number}</small></td>
                  <td>{e.category_name}</td>
                  <td>{e.start_date}</td>
                  <td>{e.end_date ?? "Open"}</td>
                  <td><StatusPill status={e.status} /></td>
                  <td>
                    <button type="button" className="link-button" onClick={() => void showHistory(e)}>History</button>{" "}
                    {e.status === "active" && <button type="button" className="link-button" onClick={() => void change(e, "suspended")}>Suspend</button>}{" "}
                    {e.status === "suspended" && <button type="button" className="link-button" onClick={() => void change(e, "active")}>Resume</button>}{" "}
                    {e.status !== "cancelled" && <button type="button" className="link-button" onClick={() => void change(e, "cancelled")}>Cancel</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {history && (
          <div role="region" aria-label="Subscription history">
            <h3>History: {history.student_name} · {history.category_name}</h3>
            <ul className="fin-chips">
              {(history.history ?? []).map((event) => (
                <li key={event.id}>{new Date(event.created_at).toLocaleString("en-GH")} · {event.action}</li>
              ))}
            </ul>
            <button type="button" className="link-button" onClick={() => setHistory(null)}>Close</button>
          </div>
        )}
      </section>
    </div>
  );
}

function UsageEntry({ ctx, unitCategories, years }: { ctx: FinanceContext; unitCategories: FeeCategory[]; years: Year[] }) {
  const students = useStudentOptions(ctx);
  const current = years.find((year) => year.is_current) ?? years[0];
  const [term, setTerm] = useState<string>(TERMS[0]);
  const usage = useFinanceData<Usage[]>(ctx, `service-usage/?period_label=${encodeURIComponent(term)}`);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function record(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "service-usage/", {
        method: "POST",
        body: {
          student: text(values, "student"),
          category: text(values, "category"),
          academic_year: text(values, "academic_year"),
          period_label: term,
          quantity: text(values, "quantity"),
        },
      });
      setFailed(false);
      setMessage("Usage saved.");
      form.reset();
      usage.reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  if (unitCategories.length === 0) return null;
  return (
    <section className="enrol-card" aria-label="Usage">
      <div className="enrol-card-head"><div><p className="eyebrow">USAGE-BASED FEES</p><h2>Record usage</h2></div></div>
      <p className="fin-note">Per-unit fees (for example meals) are billed as units × price. Usage must be recorded for the term before an invoice can be created.</p>
      <form className="fin-inline" onSubmit={record} aria-label="Record usage">
        <label>Term
          <select value={term} onChange={(event) => setTerm(event.target.value)}>{TERMS.map((t) => <option key={t} value={t}>{t}</option>)}</select>
        </label>
        <label>Academic year
          <select name="academic_year" required defaultValue={current?.id ?? ""} key={current?.id ?? "none"}>
            {years.map((y) => <option key={y.id} value={y.id}>{y.name}</option>)}
          </select>
        </label>
        <label>Student
          <select name="student" required defaultValue="">
            <option value="" disabled>Select</option>
            {students.map((s) => <option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>)}
          </select>
        </label>
        <label>Service
          <select name="category" required defaultValue="">
            <option value="" disabled>Select</option>
            {unitCategories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label>Units<input name="quantity" type="number" min={0} max={10000} step={1} required /></label>
        <button className="setup-submit" type="submit">Save usage</button>
      </form>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      {usage.data && usage.data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Student</th><th>Service</th><th className="num">Units</th></tr></thead>
          <tbody>{usage.data.map((u) => <tr key={u.id}><td>{u.student_name}</td><td>{u.category_name}</td><td className="num">{u.quantity}</td></tr>)}</tbody>
        </table>
      )}
    </section>
  );
}

function Adjustments({ ctx, categories, years }: { ctx: FinanceContext; categories: FeeCategory[]; years: Year[] }) {
  const students = useStudentOptions(ctx);
  const { data, error, loading, reload } = useFinanceData<Adjustment[]>(ctx, "adjustments/");
  const current = years.find((year) => year.is_current) ?? years[0];
  const [kind, setKind] = useState<Adjustment["kind"]>("exemption");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const reason = text(values, "reason");
    if (!reason) {
      setFailed(true);
      setMessage("A reason is required for every adjustment.");
      return;
    }
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "adjustments/", {
        method: "POST",
        body: {
          student: text(values, "student"),
          academic_year: text(values, "academic_year"),
          kind,
          reason,
          category: kind === "exemption" ? text(values, "category") : undefined,
          description: text(values, "description") || undefined,
          amount: kind === "charge" || text(values, "mode") === "amount" ? text(values, "value") || undefined : undefined,
          percent: kind !== "charge" && kind !== "exemption" && text(values, "mode") === "percent" ? text(values, "value") || undefined : undefined,
        },
      });
      setFailed(false);
      setMessage("Saved. It applies to invoices generated from now on; issued invoices are not changed.");
      form.reset();
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function revoke(adjustment: Adjustment) {
    const reason = window.prompt("Reason for revoking this adjustment?");
    if (!reason?.trim()) return;
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `adjustments/${adjustment.id}/revoke/`, { method: "POST", body: { reason: reason.trim() } });
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <div className="enrol-layout">
      <form className="enrol-card" onSubmit={save} aria-label="Add adjustment">
        <div className="enrol-card-head"><div><p className="eyebrow">EXEMPTIONS &amp; ADJUSTMENTS</p><h2>Student-specific changes</h2></div></div>
        <div className="enrol-grid">
          <label className="enrol-wide">Student
            <select name="student" required defaultValue="">
              <option value="" disabled>Select student</option>
              {students.map((s) => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} · {s.admission_number}</option>)}
            </select>
          </label>
          <label>Academic year
            <select name="academic_year" required defaultValue={current?.id ?? ""} key={current?.id ?? "none"}>
              {years.map((y) => <option key={y.id} value={y.id}>{y.name}</option>)}
            </select>
          </label>
          <label>Type
            <select value={kind} onChange={(event) => setKind(event.target.value as Adjustment["kind"])}>
              <option value="exemption">Exempt from a fee</option>
              <option value="scholarship">Scholarship</option>
              <option value="discount">Discount</option>
              <option value="charge">Extra charge</option>
            </select>
          </label>
          {kind === "exemption" ? (
            <label>Fee
              <select name="category" required defaultValue="">
                <option value="" disabled>Select fee</option>
                {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
          ) : (
            <>
              {kind !== "charge" && (
                <label>Value is a
                  <select name="mode" defaultValue="percent"><option value="percent">Percentage</option><option value="amount">Fixed amount (GHS)</option></select>
                </label>
              )}
              <label>{kind === "charge" ? "Amount (GHS)" : "Value"}<input name="value" inputMode="decimal" required placeholder="0.00" /></label>
              <label>Description<input name="description" maxLength={160} required /></label>
            </>
          )}
          <label className="enrol-wide">Reason (required)<input name="reason" maxLength={240} placeholder="Why is this being applied?" /></label>
        </div>
        {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
        <div className="enrol-actions"><button className="setup-submit" type="submit">Save</button></div>
      </form>
      <section className="enrol-card roster" aria-label="Adjustments">
        <div className="enrol-card-head"><div><p className="eyebrow">RECORD</p><h2>Applied adjustments</h2></div></div>
        <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="Nothing has been applied yet." />
        {data && data.length > 0 && (
          <table className="fin-table">
            <thead><tr><th>Student</th><th>Type</th><th>Detail</th><th>Reason</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.map((a) => (
                <tr key={a.id}>
                  <td>{a.student_name}</td>
                  <td>{a.kind}</td>
                  <td>{a.description}{a.amount && <small>{formatMoney(a.amount)}</small>}{a.percent && <small>{a.percent}%</small>}</td>
                  <td>{a.reason || "—"}</td>
                  <td><StatusPill status={a.status} /></td>
                  <td>{a.status === "approved" && <button type="button" className="link-button" onClick={() => void revoke(a)}>Revoke</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Services({ ctx }: { ctx: FinanceContext }) {
  const years = useFinanceData<Year[]>(ctx, "academic-years/");
  const categories = useFinanceData<FeeCategory[]>(ctx, "fee-categories/");
  const schedules = useFinanceData<FeeSchedule[]>(ctx, "fee-schedules/");

  const { serviceCategories, unitCategories } = useMemo(() => {
    const all = categories.data ?? [];
    const rows = (schedules.data ?? []).filter((s) => s.status === "active");
    const ids = (test: (s: FeeSchedule) => boolean) => new Set(rows.filter(test).map((s) => s.category));
    const service = ids((s) => s.applicability === "optional" || s.applicability === "individual");
    const unit = ids((s) => s.billing_basis === "per_unit");
    return {
      serviceCategories: all.filter((c) => c.is_active && service.has(c.id)),
      unitCategories: all.filter((c) => c.is_active && unit.has(c.id)),
    };
  }, [categories.data, schedules.data]);

  if (years.error || categories.error || schedules.error) {
    return <p className="setup-error" role="alert">{years.error || categories.error || schedules.error}</p>;
  }
  if (!years.data || !categories.data || !schedules.data) return <p className="roster-empty">Loading…</p>;
  return (
    <>
      <Enrollments ctx={ctx} serviceCategories={serviceCategories} />
      <UsageEntry ctx={ctx} unitCategories={unitCategories} years={years.data} />
      <Adjustments ctx={ctx} categories={categories.data.filter((c) => c.is_active)} years={years.data} />
    </>
  );
}

export default function ServicesPage() {
  return (
    <FinanceShell
      eyebrow="SERVICES"
      title="Services & exemptions"
      active="/finance/services"
      description="Subscribe students to optional services, record usage for per-unit fees, and apply exemptions or adjustments with a recorded reason."
    >
      {(ctx) => <Services ctx={ctx} />}
    </FinanceShell>
  );
}

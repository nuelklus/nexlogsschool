"use client";

import { FormEvent, useState } from "react";

import { GRADE_LEVELS } from "../grade-levels";
import { APPLICABILITY_LABELS, Applicability, FeeCategory, FeeSchedule, financeRequest, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

type Year = { id: string; name: string };

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function Schedules({ ctx, categories, years }: { ctx: FinanceContext; categories: FeeCategory[]; years: Year[] }) {
  const { data, error, loading, reload } = useFinanceData<FeeSchedule[]>(ctx, "fee-schedules/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [applicability, setApplicability] = useState<Applicability>("compulsory");
  const [basis, setBasis] = useState("fixed");
  const [conditionType, setConditionType] = useState("");
  const serviceCategories = categories.filter((c) => c.is_active);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "fee-schedules/", {
        method: "POST",
        body: {
          academic_year: text(values, "academic_year"),
          category: text(values, "category"),
          grade_level: text(values, "grade_level"),
          amount: text(values, "amount"),
          effective_from: text(values, "effective_from"),
          effective_to: text(values, "effective_to"),
          applicability,
          billing_basis: basis,
          unit_label: basis === "per_unit" ? text(values, "unit_label") : "",
          condition_type: applicability === "conditional" ? conditionType : "",
          condition_value: applicability === "conditional" ? text(values, "condition_value") : "",
        },
      });
      setFailed(false);
      setMessage("Fee schedule saved. It applies to invoices generated from now on.");
      form.reset();
      setApplicability("compulsory");
      setBasis("fixed");
      setConditionType("");
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(schedule: FeeSchedule) {
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `fee-schedules/${schedule.id}/`, {
        method: "PATCH",
        body: { status: schedule.status === "active" ? "inactive" : "active" },
      });
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <div className="enrol-layout">
      <form className="enrol-card" onSubmit={submit} aria-label="Add fee schedule">
        <div className="enrol-card-head"><div><p className="eyebrow">FEE SCHEDULE</p><h2>Set a fee</h2></div></div>
        <div className="enrol-grid">
          <label>Academic year
            <select name="academic_year" required defaultValue="">
              <option value="" disabled>Select year</option>
              {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
            </select>
          </label>
          <label>Fee category
            <select name="category" required defaultValue="">
              <option value="" disabled>Select category</option>
              {categories.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label>Class
            <select name="grade_level" defaultValue="">
              <option value="">All classes</option>
              {GRADE_LEVELS.map((level) => <option key={level} value={level}>{level}</option>)}
            </select>
          </label>
          <label>Amount (GHS){basis === "per_unit" ? " per unit" : ""}<input name="amount" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required placeholder="0.00" /></label>
          <label>Who is charged
            <select name="applicability" value={applicability} onChange={(event) => setApplicability(event.target.value as Applicability)}>
              {(Object.keys(APPLICABILITY_LABELS) as Applicability[]).map((key) => <option key={key} value={key}>{APPLICABILITY_LABELS[key]}</option>)}
            </select>
          </label>
          <label>Billed
            <select name="billing_basis" value={basis} onChange={(event) => setBasis(event.target.value)}>
              <option value="fixed">Fixed amount per term</option>
              <option value="per_unit">Per unit used (e.g. per meal)</option>
            </select>
          </label>
          {basis === "per_unit" && <label>Unit name<input name="unit_label" maxLength={30} required placeholder="e.g. meal" /></label>}
          {applicability === "conditional" && (
            <>
              <label>Condition
                <select name="condition_type" required value={conditionType} onChange={(event) => setConditionType(event.target.value)}>
                  <option value="" disabled>Select condition</option>
                  <option value="gender">Student gender</option>
                  <option value="new_student">New student this year</option>
                  <option value="service">Subscribed to a service</option>
                </select>
              </label>
              {conditionType === "gender" && (
                <label>Gender
                  <select name="condition_value" required defaultValue=""><option value="" disabled>Select</option><option value="male">Male</option><option value="female">Female</option></select>
                </label>
              )}
              {conditionType === "service" && (
                <label>Service
                  <select name="condition_value" required defaultValue="">
                    <option value="" disabled>Select service</option>
                    {serviceCategories.map((c) => <option key={c.id} value={c.code}>{c.name}</option>)}
                  </select>
                </label>
              )}
            </>
          )}
          {applicability === "optional" || applicability === "individual" ? (
            <p className="fin-note enrol-wide">Students are charged this fee only while subscribed (set under Services). Unsubscribed students are shown as excluded in the invoice preview.</p>
          ) : null}
          <label>Effective from<input name="effective_from" type="date" required /></label>
          <label>Effective to (optional)<input name="effective_to" type="date" /></label>
        </div>
        {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
        <div className="enrol-actions"><button className="setup-submit" type="submit" disabled={busy}>{busy ? "Saving…" : "Save fee schedule"}</button></div>
      </form>
      <section className="enrol-card roster" aria-label="Fee schedules">
        <div className="enrol-card-head"><div><p className="eyebrow">CURRENT FEES</p><h2>Fee schedules</h2></div></div>
        <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No fees have been set up yet." />
        {data && data.length > 0 && (
          <table className="fin-table">
            <thead><tr><th>Category</th><th>Class</th><th>Charged</th><th className="num">Amount</th><th>From</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.map((schedule) => (
                <tr key={schedule.id}>
                  <td>{schedule.category_name}</td>
                  <td>{schedule.grade_level || "All classes"}</td>
                  <td>
                    {APPLICABILITY_LABELS[schedule.applicability] ?? schedule.applicability}
                    {schedule.condition_type && <small>{schedule.condition_type.replaceAll("_", " ")}{schedule.condition_value ? `: ${schedule.condition_value}` : ""}</small>}
                  </td>
                  <td className="num">{formatMoney(schedule.amount)}{schedule.billing_basis === "per_unit" && <small>per {schedule.unit_label}</small>}</td>
                  <td>{schedule.effective_from}</td>
                  <td><StatusPill status={schedule.status} /></td>
                  <td><button type="button" className="link-button" onClick={() => void toggle(schedule)}>{schedule.status === "active" ? "Deactivate" : "Activate"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Categories({ ctx, categories, reload }: { ctx: FinanceContext; categories: FeeCategory[]; reload: () => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const accounts = useFinanceData<{ id: string; code: string; name: string; type: string }[]>(ctx, "accounts/");
  const income = accounts.data?.filter((account) => account.type === "income") ?? [];
  const [accountMessage, setAccountMessage] = useState("");
  const [accountDone, setAccountDone] = useState("");

  async function addAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setAccountMessage("");
    setAccountDone("");
    try {
      const created = await financeRequest<{ code: string; name: string }>(ctx.authFetch, ctx.tenantId, "accounts/", {
        method: "POST",
        body: { code: text(values, "code"), name: text(values, "name"), type: "income" },
      });
      form.reset();
      setAccountDone(`Added ${created.code} ${created.name}. Choose it above.`);
      accounts.reload();
    } catch (caught) {
      setAccountMessage(errorText(caught));
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "fee-categories/", {
        method: "POST",
        body: { code: text(values, "code"), name: text(values, "name"), income_account: text(values, "income_account") },
      });
      form.reset();
      reload();
    } catch (caught) {
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="enrol-card" aria-label="Fee categories">
      <div className="enrol-card-head"><div><p className="eyebrow">CATEGORIES</p><h2>Fee categories</h2></div></div>
      <ul className="fin-chips">
        {categories.map((category) => <li key={category.id}>{category.name}</li>)}
      </ul>
      <form className="fin-inline" onSubmit={submit} aria-label="Add fee category">
        <label>Name<input name="name" maxLength={100} required /></label>
        <label>Code<input name="code" maxLength={30} required placeholder="e.g. sports" /></label>
        <label>Income account
          <select name="income_account" required defaultValue="">
            <option value="" disabled>Select</option>
            {income.map((account) => <option key={account.id} value={account.id}>{account.code} {account.name}</option>)}
          </select>
        </label>
        <button className="setup-submit" type="submit" disabled={busy}>Add category</button>
      </form>
      {message && <p className="setup-error" role="alert">{message}</p>}
      {ctx.canApprove && (
        <form className="fin-inline" onSubmit={addAccount} aria-label="Add income account">
          <label>New income account code<input name="code" maxLength={20} required placeholder="e.g. 4050" /></label>
          <label>Account name<input name="name" maxLength={160} required placeholder="e.g. Lunch fees" /></label>
          <button className="auth-signout" type="submit">Add income account</button>
        </form>
      )}
      <p className="fin-note">Not in the list? An income account is where this fee is recorded as earned. Add one here, then pick it above.</p>
      {accountDone && <p className="enrol-success" role="status">{accountDone}</p>}
      {accountMessage && <p className="setup-error" role="alert">{accountMessage}</p>}
    </section>
  );
}

function Years({ ctx, reload }: { ctx: FinanceContext; reload: () => void }) {
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "academic-years/", {
        method: "POST",
        body: { name: text(values, "name"), start_date: text(values, "start_date"), end_date: text(values, "end_date"), is_current: values.get("is_current") === "on" },
      });
      form.reset();
      reload();
    } catch (caught) {
      setMessage(errorText(caught));
    }
  }
  return (
    <form className="fin-inline" onSubmit={submit} aria-label="Add academic year">
      <label>Name<input name="name" maxLength={120} required placeholder="2026/2027" /></label>
      <label>Starts<input name="start_date" type="date" required /></label>
      <label>Ends<input name="end_date" type="date" required /></label>
      <label className="fin-check"><input name="is_current" type="checkbox" /> Current year</label>
      <button className="setup-submit" type="submit">Add year</button>
      {message && <p className="setup-error" role="alert">{message}</p>}
    </form>
  );
}

function Fees({ ctx }: { ctx: FinanceContext }) {
  const categories = useFinanceData<FeeCategory[]>(ctx, "fee-categories/");
  const years = useFinanceData<Year[]>(ctx, "academic-years/");
  return (
    <>
      <LoadState loading={categories.loading} error={categories.error} />
      {categories.data && (
        <>
          {years.data?.length === 0 && (
            <section className="enrol-card" aria-label="Academic years">
              <h2>Add your first academic year</h2>
              <p className="roster-empty">Fees are set per academic year.</p>
              {ctx.canApprove ? <Years ctx={ctx} reload={years.reload} /> : <p>Ask the school owner or administrator to add one.</p>}
            </section>
          )}
          <Categories ctx={ctx} categories={categories.data} reload={categories.reload} />
          <Schedules ctx={ctx} categories={categories.data} years={years.data ?? []} />
        </>
      )}
    </>
  );
}

export default function FeesPage() {
  return (
    <FinanceShell
      eyebrow="FEE SETUP"
      title="Fees"
      active="/finance/fees"
      description="Define what each class pays. Changing a fee only affects invoices generated afterwards; issued invoices keep their amounts."
    >
      {(ctx) => <Fees ctx={ctx} />}
    </FinanceShell>
  );
}

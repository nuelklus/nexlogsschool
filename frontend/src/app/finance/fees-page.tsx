"use client";

import { FormEvent, useEffect, useState } from "react";

import { SchoolClassLevel, listClassLevels } from "../academics/academics-api";
import { APPLICABILITY_LABELS, Applicability, FeeCategory, FeeSchedule, financeRequest, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

type Year = { id: string; name: string };

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function Schedules({ ctx, categories, years }: { ctx: FinanceContext; categories: FeeCategory[]; years: Year[] }) {
  const [classLevels, setClassLevels] = useState<SchoolClassLevel[] | null>(null);
  const [classLevelError, setClassLevelError] = useState("");
  const { data, error, loading, reload } = useFinanceData<FeeSchedule[]>(ctx, "fee-schedules/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [applicability, setApplicability] = useState<Applicability>("compulsory");
  const [basis, setBasis] = useState("fixed");
  const [conditionType, setConditionType] = useState("");
  const [yearFilter, setYearFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [query, setQuery] = useState("");
  const [toggleBusy, setToggleBusy] = useState("");
  const [toggleMessage, setToggleMessage] = useState("");
  const [toggleError, setToggleError] = useState("");
  const [levelAttempt, setLevelAttempt] = useState(0);
  const serviceCategories = categories.filter((c) => c.is_active);

  useEffect(() => {
    let active = true;
    listClassLevels(ctx.authFetch, ctx.tenantId)
      .then((levels) => {
        if (active) {
          setClassLevels(levels.filter((level) => level.is_active));
          setClassLevelError("");
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setClassLevels(null);
          setClassLevelError(error instanceof Error ? error.message : "Class levels could not be loaded.");
        }
      });
    return () => { active = false; };
  }, [ctx.authFetch, ctx.tenantId, levelAttempt]);

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
          class_level: text(values, "class_level") || undefined,
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
    if (toggleBusy) return;
    setToggleBusy(schedule.id);
    setToggleError("");
    setToggleMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `fee-schedules/${schedule.id}/`, {
        method: "PATCH",
        body: { status: schedule.status === "active" ? "inactive" : "active" },
      });
      setToggleMessage(`${schedule.category_name} fee schedule ${schedule.status === "active" ? "deactivated" : "activated"}.`);
      reload();
    } catch (caught) {
      setToggleError(errorText(caught));
    } finally {
      setToggleBusy("");
    }
  }

  const visibleSchedules = (data ?? []).filter((schedule) =>
    (!yearFilter || schedule.academic_year === yearFilter)
    && (!statusFilter || schedule.status === statusFilter)
    && `${schedule.category_name} ${schedule.class_level_name} ${schedule.grade_level}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  );

  return (
    <div className="fees-sections">
      <form className="enrol-card fee-form" id="set-fee" onSubmit={submit} aria-label="Add fee schedule">
        <div className="enrol-card-head"><div><p className="eyebrow">STEP 2 · FEE SCHEDULE</p><h2>Set a fee</h2></div></div>
        <p className="fin-note">Choose what to charge, who it applies to, and when it takes effect. Saving does not create an invoice.</p>
        {years.length === 0 && <p className="fee-notice">Add an academic year before setting a fee.</p>}
        {serviceCategories.length === 0 && <p className="fee-notice">Add an active fee category above before setting a fee.</p>}
        <fieldset className="enrol-fieldset">
          <legend>Fee details</legend>
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
          <label>Class level
            <select name="class_level" defaultValue="" disabled={!classLevels}>
              <option value="">All class levels</option>
              {classLevels?.map((level) => (
                <option key={level.id} value={level.id}>{level.display_name}</option>
              ))}
            </select>
          </label>
          <label>Amount (GHS){basis === "per_unit" ? " per unit" : ""}<input name="amount" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required placeholder="0.00" /></label>
          </div>
        </fieldset>
        <fieldset className="enrol-fieldset">
          <legend>Charging rules</legend>
          <div className="enrol-grid">
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
            <p className="fee-notice enrol-wide">Students are charged only while subscribed under Finance → Services. Unsubscribed students appear as excluded in the invoice preview.</p>
          ) : null}
          {basis === "per_unit" && <p className="fee-notice enrol-wide">Record the student&apos;s usage under Finance → Services before billing.</p>}
          </div>
        </fieldset>
        <fieldset className="enrol-fieldset">
          <legend>Effective dates</legend>
          <div className="enrol-grid">
          <label>Effective from<input name="effective_from" type="date" required /></label>
          <label>Effective to (optional)<input name="effective_to" type="date" /></label>
          </div>
        </fieldset>
        <p className="fin-note">Class-specific schedules use the configured class level. Leave the end date blank if there is no end date.</p>
        {!classLevels && !classLevelError && <p role="status" className="fin-note">Loading class levels…</p>}
        {classLevelError && <div><p className="setup-error" role="alert">{classLevelError}</p><button className="auth-signout" type="button" onClick={() => { setClassLevelError(""); setLevelAttempt((value) => value + 1); }}>Retry class levels</button></div>}
        {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
        <div className="enrol-actions"><button className="setup-submit" type="submit" disabled={busy || !classLevels || years.length === 0 || serviceCategories.length === 0}>{busy ? "Saving…" : "Save fee schedule"}</button></div>
      </form>
      <section className="enrol-card fee-schedules" id="fee-schedules" aria-label="Fee schedules">
        <div className="enrol-card-head"><div><p className="eyebrow">STEP 3 · REVIEW</p><h2>Fee schedules</h2></div></div>
        <p className="fin-note">Review schedules by year, class and status before generating invoices. Deactivated schedules are not used for new billing.</p>
        <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No fees have been set up yet." />
        {error && <button className="auth-signout" type="button" onClick={reload}>Retry fee schedules</button>}
        {toggleError && <p className="setup-error" role="alert">{toggleError}</p>}
        {toggleMessage && <p className="enrol-success" role="status">{toggleMessage}</p>}
        {data && data.length > 0 && (
          <>
          <div className="fee-filters">
            <label>Search fees<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Category or class level" /></label>
            <label>Filter by academic year<select value={yearFilter} onChange={(event) => setYearFilter(event.target.value)}><option value="">All years</option>{years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}</select></label>
            <label>Filter by status<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select></label>
          </div>
          <p className="fin-note" aria-live="polite">Showing {visibleSchedules.length} of {data.length} schedules.</p>
          {visibleSchedules.length === 0 ? <p className="roster-empty">No schedules match these filters.</p> : (
          <div className="fee-table-scroll" role="region" aria-label="Fee schedules table" tabIndex={0}>
          <table className="fin-table">
            <thead><tr><th>Category</th><th>Academic year</th><th>Class level</th><th>Charged</th><th className="num">Amount</th><th>Effective dates</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>
              {visibleSchedules.map((schedule) => (
                <tr key={schedule.id}>
                  <td>{schedule.category_name}</td>
                  <td>{years.find((year) => year.id === schedule.academic_year)?.name ?? "Unknown year"}</td>
                  <td>{schedule.class_level_name || schedule.grade_level || "All classes"}</td>
                  <td>
                    {APPLICABILITY_LABELS[schedule.applicability] ?? schedule.applicability}
                    {schedule.condition_type && <small>{schedule.condition_type.replaceAll("_", " ")}{schedule.condition_value ? `: ${schedule.condition_value}` : ""}</small>}
                  </td>
                  <td className="num">{formatMoney(schedule.amount)}{schedule.billing_basis === "per_unit" && <small>per {schedule.unit_label}</small>}</td>
                  <td>{schedule.effective_from}<small>{schedule.effective_to ? `to ${schedule.effective_to}` : "No end date"}</small></td>
                  <td><StatusPill status={schedule.status} /></td>
                  <td><button type="button" className="link-button" disabled={Boolean(toggleBusy)} aria-label={`${schedule.status === "active" ? "Deactivate" : "Activate"} ${schedule.category_name} for ${schedule.class_level_name || schedule.grade_level || "all classes"} in ${years.find((year) => year.id === schedule.academic_year)?.name ?? "unknown year"}, effective ${schedule.effective_from}`} onClick={() => void toggle(schedule)}>{toggleBusy === schedule.id ? "Saving…" : schedule.status === "active" ? "Deactivate" : "Activate"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>)}
          </>
        )}
      </section>
    </div>
  );
}

function Categories({ ctx, categories, reload }: { ctx: FinanceContext; categories: FeeCategory[]; reload: () => void }) {
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [accountBusy, setAccountBusy] = useState(false);
  const accounts = useFinanceData<{ id: string; code: string; name: string; type: string }[]>(ctx, "accounts/");
  const income = accounts.data?.filter((account) => account.type === "income") ?? [];
  const [accountMessage, setAccountMessage] = useState("");
  const [accountDone, setAccountDone] = useState("");

  async function addAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (accountBusy) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setAccountMessage("");
    setAccountDone("");
    setAccountBusy(true);
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
    } finally {
      setAccountBusy(false);
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    setSuccess("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "fee-categories/", {
        method: "POST",
        body: { code: text(values, "code"), name: text(values, "name"), income_account: text(values, "income_account") },
      });
      form.reset();
      setSuccess("Fee category added.");
      reload();
    } catch (caught) {
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="enrol-card fee-categories" id="fee-categories" aria-label="Fee categories">
      <div className="enrol-card-head"><div><p className="eyebrow">STEP 1 · CATEGORIES</p><h2>Fee categories</h2></div></div>
      <p className="fin-note">Categories describe what the fee is for, such as tuition or transport. Each category is linked to an income account.</p>
      {categories.length ? <ul className="fee-category-list">
        {categories.map((category) => <li key={category.id}><strong>{category.name}</strong><span>{category.code}{category.is_active ? "" : " · Inactive"}</span></li>)}
      </ul> : <p className="roster-empty">No fee categories yet. Add one to set a fee.</p>}
      <form className="fee-category-form" onSubmit={submit} aria-label="Add fee category">
        <h3>Add a fee category</h3>
        <div className="enrol-grid">
        <label>Name<input name="name" maxLength={100} required /></label>
        <label>Code<input name="code" maxLength={30} required placeholder="e.g. sports" /></label>
        <label>Income account
          <select name="income_account" required defaultValue="">
            <option value="" disabled>Select</option>
            {income.map((account) => <option key={account.id} value={account.id}>{account.code} {account.name}</option>)}
          </select>
        </label>
        </div>
        {accounts.loading && <p className="fin-note" role="status">Loading income accounts…</p>}
        {accounts.error && <div><p className="setup-error" role="alert">{accounts.error}</p><button className="auth-signout" type="button" onClick={accounts.reload}>Retry income accounts</button></div>}
        {!accounts.loading && !accounts.error && income.length === 0 && <p className="fee-notice">{ctx.canApprove ? "Add an income account below before creating a category." : "Ask an owner or administrator to add an income account."}</p>}
        <button className="setup-submit" type="submit" disabled={busy || accounts.loading || Boolean(accounts.error) || income.length === 0}>{busy ? "Adding…" : "Add category"}</button>
      </form>
      {message && <p className="setup-error" role="alert">{message}</p>}
      {success && <p className="enrol-success" role="status">{success}</p>}
      {ctx.canApprove && (
        <form className="fee-account-form" onSubmit={addAccount} aria-label="Add income account">
          <h3>Add an income account</h3>
          <p className="fin-note">An income account records where this fee is earned. Add one here if it is not already listed.</p>
          <div className="enrol-grid">
          <label>New income account code<input name="code" maxLength={20} required placeholder="e.g. 4050" /></label>
          <label>Account name<input name="name" maxLength={160} required placeholder="e.g. Lunch fees" /></label>
          </div>
          <button className="auth-signout" type="submit" disabled={accountBusy}>{accountBusy ? "Adding…" : "Add income account"}</button>
        </form>
      )}
      {accountDone && <p className="enrol-success" role="status">{accountDone}</p>}
      {accountMessage && <p className="setup-error" role="alert">{accountMessage}</p>}
    </section>
  );
}

function Years({ ctx, reload }: { ctx: FinanceContext; reload: () => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setMessage("");
    setBusy(true);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "academic-years/", {
        method: "POST",
        body: { name: text(values, "name"), start_date: text(values, "start_date"), end_date: text(values, "end_date"), is_current: values.get("is_current") === "on" },
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
    <form className="fee-year-form" onSubmit={submit} aria-label="Add academic year">
      <div className="enrol-grid">
      <label>Name<input name="name" maxLength={120} required placeholder="2026/2027" /></label>
      <label>Starts<input name="start_date" type="date" required /></label>
      <label>Ends<input name="end_date" type="date" required /></label>
      </div>
      <label className="fin-check"><input name="is_current" type="checkbox" /> Current year</label>
      <button className="setup-submit" type="submit" disabled={busy}>{busy ? "Adding…" : "Add year"}</button>
      {message && <p className="setup-error" role="alert">{message}</p>}
    </form>
  );
}

function Fees({ ctx }: { ctx: FinanceContext }) {
  const categories = useFinanceData<FeeCategory[]>(ctx, "fee-categories/");
  const years = useFinanceData<Year[]>(ctx, "academic-years/");
  return (
    <div className="fees-page">
      <nav className="fees-workflow" aria-label="Fee setup steps">
        <a href="#fee-categories"><strong>1. Fee categories</strong><span>Choose or add what you charge for</span></a>
        <a href="#set-fee"><strong>2. Set a fee</strong><span>Choose the amount and rules</span></a>
        <a href="#fee-schedules"><strong>3. Review schedules</strong><span>Check dates and status before billing</span></a>
      </nav>
      <LoadState loading={categories.loading} error={categories.error} />
      {categories.error && <button className="auth-signout" type="button" onClick={categories.reload}>Retry fee categories</button>}
      <LoadState loading={years.loading} error={years.error} />
      {years.error && <button className="auth-signout" type="button" onClick={years.reload}>Retry academic years</button>}
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
          {years.data && <Schedules ctx={ctx} categories={categories.data} years={years.data} />}
        </>
      )}
    </div>
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

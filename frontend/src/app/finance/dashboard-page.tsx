"use client";

import Link from "next/link";
import { useState } from "react";

import { Summary } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, Money, useFinanceData } from "./finance-shell";

function Breakdown({ title, rows }: { title: string; rows: { label: string; amount: string }[] }) {
  return (
    <section className="enrol-card" aria-label={title}>
      <h2>{title}</h2>
      {rows.length === 0 ? (
        <p className="roster-empty">No confirmed collections in this period.</p>
      ) : (
        <table className="fin-table">
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td className="num"><Money value={row.amount} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Dashboard({ ctx }: { ctx: FinanceContext }) {
  const [range, setRange] = useState({ start: "", end: "" });
  const query = new URLSearchParams(Object.entries(range).filter(([, value]) => value)).toString();
  const { data, error, loading } = useFinanceData<Summary>(ctx, `reports/summary/${query ? `?${query}` : ""}`);

  return (
    <>
      <form className="fin-filter" aria-label="Report period" onSubmit={(event) => event.preventDefault()}>
        <label>From<input type="date" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></label>
        <label>To<input type="date" value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })} /></label>
        <button type="button" className="auth-signout" onClick={() => setRange({ start: "", end: "" })}>All time</button>
      </form>
      <LoadState loading={loading} error={error} />
      {data && (
        <>
          <section className="stats-row" aria-label="Finance summary">
            <article className="stat-block"><span className="stat-label">INVOICED</span><strong><Money value={data.total_invoiced} /></strong><span className="stat-note">issued invoices</span></article>
            <article className="stat-block"><span className="stat-label">COLLECTED</span><strong><Money value={data.total_collected} /></strong><span className="stat-note">confirmed payments</span></article>
            <article className="stat-block"><span className="stat-label">OUTSTANDING</span><strong><Money value={data.outstanding_balance} /></strong><span className="stat-note">as of today</span></article>
            <article className="stat-block"><span className="stat-label">OVERDUE</span><strong><Money value={data.overdue_balance} /></strong><span className="stat-note">{data.overdue_invoices} invoice(s)</span></article>
          </section>
          <p className="fin-note">
            Pending mobile-money payments ({" "}<Money value={data.pending_unconfirmed} />) are not counted as collected until confirmed.
            Advance payments held for students: <Money value={data.student_advances_held} />.
          </p>
          <div className="fin-grid">
            <Breakdown title="Collections by method" rows={data.collections_by_method.map((r) => ({ label: r.method, amount: r.amount }))} />
            <Breakdown title="Collections by campus" rows={data.collections_by_campus.map((r) => ({ label: r.campus, amount: r.amount }))} />
            <Breakdown title="Collections by class" rows={data.collections_by_class.map((r) => ({ label: r.grade_level, amount: r.amount }))} />
            <Breakdown title="Collections by date" rows={data.collections_by_date.map((r) => ({ label: r.date, amount: r.amount }))} />
          </div>
        </>
      )}
    </>
  );
}

export default function FinanceDashboardPage() {
  return (
    <FinanceShell
      eyebrow="FINANCE"
      title="Dashboard"
      active="/finance"
      description="Fees invoiced, money collected and what is still owed. Collections count confirmed payments only."
      actions={
        <div className="heading-actions">
          <Link className="setup-submit" href="/finance/payments">Record payment</Link>
          <Link className="auth-signout" href="/finance/billing">Bill students</Link>
        </div>
      }
    >
      {(ctx) => <Dashboard ctx={ctx} />}
    </FinanceShell>
  );
}

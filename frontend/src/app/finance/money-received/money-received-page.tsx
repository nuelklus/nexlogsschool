"use client";

import { useState } from "react";

import { ClassOptions, ClassOptionsStatus, useClassOptions } from "../../class-options";
import { PaymentMethod, formatMoney } from "../finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "../finance-shell";
import type { IncomeCategory, MoneyReceived } from "../income/income-api";

const KINDS = [
  { value: "cash", label: "Cash" },
  { value: "bank_transfer", label: "Bank transfer" },
  { value: "cheque", label: "Cheque" },
  { value: "mobile_money", label: "Mobile money" },
];

type Filters = { start: string; end: string; kind: string; method: string; source: string; category: string; grade_level: string; status: string };
const EMPTY: Filters = { start: "", end: "", kind: "", method: "", source: "", category: "", grade_level: "", status: "confirmed" };

function Breakdown({ title, rows }: { title: string; rows: { label: string; amount: string }[] }) {
  return (
    <section className="enrol-card" aria-label={title}>
      <h2>{title}</h2>
      {rows.length === 0 ? <p className="roster-empty">Nothing received.</p> : (
        <table className="fin-table"><tbody>{rows.map((row) => <tr key={row.label}><td>{row.label}</td><td className="num">{formatMoney(row.amount)}</td></tr>)}</tbody></table>
      )}
    </section>
  );
}

function Report({ ctx }: { ctx: FinanceContext }) {
  const classes = useClassOptions(ctx.authFetch, ctx.tenantId, "finance");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const methods = useFinanceData<PaymentMethod[]>(ctx, ctx.canApprove ? "payment-methods/" : null);
  const categories = useFinanceData<IncomeCategory[]>(ctx, ctx.canApprove ? "income-categories/" : null);
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  const { data, error, loading } = useFinanceData<MoneyReceived>(ctx, `reports/money-received/?${query}`);
  const set = (key: keyof Filters) => (event: { target: { value: string } }) => setFilters({ ...filters, [key]: event.target.value });
  const studentOnly = Boolean(filters.grade_level);

  return (
    <div className="fin-stack">
      <section className="enrol-card" aria-label="Filters">
        {!ctx.canApprove && <p className="fin-note">Only allocations to invoices in your authorized campuses are shown. Unallocated advances, refunds, pending payments and other income are excluded.</p>}
        <p className="fin-note">Class filters show only amounts allocated to invoices for that class, not unallocated advances or refunds. Later allocations can change the results.</p>
        <div className="enrol-grid">
          <label>From<input type="date" value={filters.start} onChange={set("start")} /></label>
          <label>To<input type="date" value={filters.end} onChange={set("end")} /></label>
          <label>Received by
            <select value={filters.kind} onChange={set("kind")}>
              <option value="">All types</option>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
          {ctx.canApprove && <label>Account / method
            <select value={filters.method} onChange={set("method")}>
              <option value="">All methods</option>
              {methods.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>}
          {ctx.canApprove && <label>Source
            <select value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value, category: "" })}>
              <option value="">Everything</option>
              <option value="fees">Student fees</option>
              <option value="other">Other income</option>
            </select>
          </label>}
          {ctx.canApprove && <label>Income type
            <select value={filters.category} disabled={filters.source === "fees" || studentOnly} onChange={set("category")}>
              <option value="">All income types</option>
              {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>}
          <label>Class (fees only)
            <select value={filters.grade_level} disabled={!classes.options} onChange={(event) => setFilters({ ...filters, grade_level: event.target.value, category: "" })}>
              <option value="">All classes</option>
              <ClassOptions options={classes.options} />
            </select>
          </label>
          <label>Status
            <select value={filters.status} onChange={set("status")}>
              <option value="confirmed">Confirmed</option>
              {ctx.canApprove && <option value="pending">Pending (mobile money)</option>}
              <option value="reversed">Reversed</option>
            </select>
          </label>
        </div>
        <ClassOptionsStatus options={classes.options} error={classes.error} />
        <button type="button" className="link-button" onClick={() => setFilters(EMPTY)}>Clear filters</button>
      </section>
      <LoadState loading={loading} error={error} />
      {data && (
        <>
          <section className="stats-row" aria-label="Totals">
            <article className="stat-block"><span className="stat-label">TOTAL RECEIVED</span><strong>{formatMoney(data.total)}</strong><span className="stat-note">{data.count} receipts</span></article>
            {data.by_kind.map((row) => (
              <article className="stat-block" key={row.kind}><span className="stat-label">{row.kind.toUpperCase()}</span><strong>{formatMoney(row.amount)}</strong></article>
            ))}
          </section>
          <div className="fin-grid">
            <Breakdown title="By source" rows={data.by_source.map((r) => ({ label: r.source, amount: r.amount }))} />
            <Breakdown title="By method" rows={data.by_method.map((r) => ({ label: r.method, amount: r.amount }))} />
          </div>
          <section className="enrol-card roster" aria-label="Receipts">
            <h2>Receipts</h2>
            {data.rows.length === 0 ? <p className="roster-empty">No receipts match these filters.</p> : (
              <table className="fin-table">
                <thead><tr><th>Date</th><th>Source</th><th>From</th><th>Method</th><th>Reference</th><th className="num">Amount</th></tr></thead>
                <tbody>
                  {data.rows.map((row, index) => (
                    <tr key={`${row.date}-${row.reference}-${index}`}>
                      <td>{row.date}</td>
                      <td>{row.category}</td>
                      <td>{row.from || "—"}<small>{row.grade_level}</small></td>
                      <td>{row.method}</td>
                      <td>{row.reference || "—"}</td>
                      <td className="num">{formatMoney(row.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {data.truncated && <p className="fin-note">Showing the latest 500 receipts. Narrow the dates to see the rest; the totals above include everything.</p>}
          </section>
        </>
      )}
    </div>
  );
}

export default function MoneyReceivedPage() {
  return (
    <FinanceShell
      eyebrow="REPORTING"
      title="Money received"
      active="/finance/money-received"
      description="Everything the school received, from student fees and other income, by cash, bank, cheque or mobile money."
    >
      {(ctx) => <Report ctx={ctx} />}
    </FinanceShell>
  );
}

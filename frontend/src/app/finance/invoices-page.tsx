"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Invoice, financeRequest, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");

function InvoiceList({ ctx }: { ctx: FinanceContext }) {
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const invoices = useFinanceData<Invoice[]>(ctx, `invoices/${status ? `?status=${status}` : ""}`);
  const [query, setQuery] = useState("");

  async function issue(invoice: Invoice) {
    setError("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `invoices/${invoice.id}/issue/`, { method: "POST" });
      invoices.reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (invoices.data ?? []).filter((invoice) =>
      !needle || `${invoice.student_name} ${invoice.number} ${invoice.admission_number}`.toLowerCase().includes(needle),
    );
  }, [invoices.data, query]);

  return (
    <section className="enrol-card roster" aria-label="Invoices">
      <div className="enrol-card-head">
        <div><p className="eyebrow">INVOICE REGISTER</p><h2>All invoices</h2></div>
        <div className="fin-inline-controls">
          <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {["draft", "issued", "partially_paid", "paid", "credited", "voided"].map((item) => (
              <option key={item} value={item}>{item.replaceAll("_", " ")}</option>
            ))}
          </select>
          <input
            className="roster-search"
            type="search"
            placeholder="Search student or invoice"
            aria-label="Search invoices"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {error && <p className="setup-error" role="alert">{error}</p>}
      <LoadState loading={invoices.loading} error={invoices.error} empty={invoices.data?.length === 0} emptyText="No invoices yet. Create the first draft under Billing." />
      {visible.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Invoice</th><th>Student</th><th>Due</th><th className="num">Total</th><th className="num">Balance</th><th>Status</th><th /></tr></thead>
          <tbody>
            {visible.map((invoice) => (
              <tr key={invoice.id}>
                <td><Link href={`/finance/invoices/${invoice.id}`}>{invoice.number || "Draft"}</Link></td>
                <td>{invoice.student_name}<small>{invoice.period_label}</small></td>
                <td>{invoice.due_date}</td>
                <td className="num">{formatMoney(invoice.total)}</td>
                <td className="num">{formatMoney(invoice.balance)}</td>
                <td><StatusPill status={invoice.overdue ? "overdue" : invoice.status} /></td>
                <td>{invoice.status === "draft" && <button type="button" className="link-button" onClick={() => void issue(invoice)}>Issue</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function InvoicesPage() {
  return (
    <FinanceShell
      eyebrow="BILLING"
      title="Invoices"
      active="/finance/invoices"
      description="Search and review invoices, filter by status, and issue drafts."
    >
      {(ctx) => <InvoiceList ctx={ctx} />}
    </FinanceShell>
  );
}

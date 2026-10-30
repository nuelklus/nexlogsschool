"use client";

import { useState } from "react";

import { Transaction, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

function Transactions({ ctx }: { ctx: FinanceContext }) {
  const [range, setRange] = useState({ start: "", end: "" });
  const query = new URLSearchParams(Object.entries(range).filter(([, value]) => value)).toString();
  const { data, error, loading } = useFinanceData<Transaction[]>(ctx, `reports/transactions/${query ? `?${query}` : ""}`);
  return (
    <section className="enrol-card roster" aria-label="Transaction history">
      <div className="enrol-card-head">
        <div><p className="eyebrow">LEDGER</p><h2>Financial transactions</h2></div>
        <div className="fin-inline-controls">
          <label>From<input type="date" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></label>
          <label>To<input type="date" value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })} /></label>
        </div>
      </div>
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No ledger entries in this period." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Date</th><th>Entry</th><th>Account</th><th>Description</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead>
          <tbody>
            {data.map((row, index) => (
              <tr key={`${row.entry}-${row.account}-${index}`}>
                <td>{row.date}</td>
                <td>{row.entry}</td>
                <td>{row.account}</td>
                <td>{row.memo}</td>
                <td className="num">{row.debit === "0.00" ? "" : formatMoney(row.debit)}</td>
                <td className="num">{row.credit === "0.00" ? "" : formatMoney(row.credit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function TransactionsPage() {
  return (
    <FinanceShell eyebrow="LEDGER" title="Transactions" active="/finance/transactions" description="Posted journal lines, newest first. Posted entries are never edited; corrections appear as reversing entries.">
      {(ctx) => <Transactions ctx={ctx} />}
    </FinanceShell>
  );
}

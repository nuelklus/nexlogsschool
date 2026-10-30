"use client";

import Link from "next/link";
import { useState } from "react";

import { ArrearsRow, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

function Arrears({ ctx }: { ctx: FinanceContext }) {
  const [overdueOnly, setOverdueOnly] = useState(false);
  const { data, error, loading } = useFinanceData<ArrearsRow[]>(ctx, `reports/outstanding/${overdueOnly ? "?overdue=true" : ""}`);
  return (
    <section className="enrol-card roster" aria-label="Outstanding fees">
      <div className="enrol-card-head">
        <div><p className="eyebrow">OUTSTANDING</p><h2>Students who owe fees</h2></div>
        <label className="fin-check"><input type="checkbox" checked={overdueOnly} onChange={(event) => setOverdueOnly(event.target.checked)} /> Overdue only</label>
      </div>
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No outstanding balances. Every issued invoice is settled." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Student</th><th>Class</th><th className="num">Invoices</th><th>Oldest due</th><th className="num">Overdue</th><th className="num">Balance</th><th /></tr></thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.student}>
                <td>{row.student_name}<small>{row.admission_number}</small></td>
                <td>{row.grade_level || "—"}</td>
                <td className="num">{row.invoices}</td>
                <td>{row.oldest_due_date}</td>
                <td className="num">{formatMoney(row.overdue_balance)}</td>
                <td className="num">{formatMoney(row.balance)}</td>
                <td><Link href={`/finance/statements/${row.student}`}>Statement</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function ArrearsPage() {
  return (
    <FinanceShell eyebrow="COLLECTIONS" title="Arrears" active="/finance/arrears" description="Outstanding balances on issued invoices, largest first. Overdue means the due date has passed.">
      {(ctx) => <Arrears ctx={ctx} />}
    </FinanceShell>
  );
}

"use client";

import { useParams } from "next/navigation";

import { Statement, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

function StatementView({ ctx, id }: { ctx: FinanceContext; id: string }) {
  const { data, error, loading } = useFinanceData<Statement>(ctx, `students/${id}/statement/`);
  if (!data) return <LoadState loading={loading} error={error} />;
  return (
    <article className="enrol-card print-sheet" aria-label="Account statement">
      <div className="enrol-card-head">
        <div>
          <p className="eyebrow">ACCOUNT STATEMENT</p>
          <h2>{data.student.name}</h2>
          <p className="fin-note">{data.student.admission_number} · {data.student.grade_level || "No class"}</p>
        </div>
      </div>
      <dl className="fin-facts">
        <div><dt>Balance owed</dt><dd>{formatMoney(data.closing_balance)}</dd></div>
        <div><dt>Outstanding invoices</dt><dd>{formatMoney(data.outstanding_invoices)}</dd></div>
        <div><dt>Advance credit</dt><dd>{formatMoney(data.advance_credit)}</dd></div>
      </dl>
      {data.entries.length === 0 ? (
        <p className="roster-empty">No transactions on this account yet.</p>
      ) : (
        <table className="fin-table">
          <thead><tr><th>Date</th><th>Details</th><th className="num">Charges</th><th className="num">Payments / credits</th><th className="num">Balance</th></tr></thead>
          <tbody>
            {data.entries.map((entry, index) => (
              <tr key={`${entry.reference}-${entry.type}-${index}`}>
                <td>{entry.date}</td>
                <td>{entry.description}</td>
                <td className="num">{entry.debit === "0.00" ? "" : formatMoney(entry.debit)}</td>
                <td className="num">{entry.credit === "0.00" ? "" : formatMoney(entry.credit)}</td>
                <td className="num">{formatMoney(entry.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="enrol-actions no-print"><button type="button" className="auth-signout" onClick={() => window.print()}>Print statement</button></div>
    </article>
  );
}

export default function StatementPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <FinanceShell eyebrow="STUDENTS" title="Statement" active="/finance/invoices" description="Every invoice, payment, credit and refund on this student's account.">
      {(ctx) => <StatementView ctx={ctx} id={id} />}
    </FinanceShell>
  );
}

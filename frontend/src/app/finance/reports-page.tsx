"use client";

import { TrialBalance, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

function TrialBalanceReport({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading } = useFinanceData<TrialBalance>(ctx, "reports/trial-balance/");
  return (
    <section className="enrol-card roster" aria-label="Trial balance report">
      <div className="enrol-card-head"><div><p className="eyebrow">LEDGER CONTROL</p><h2>Trial balance</h2></div></div>
      <p className="fin-note">Debits and credits are net balances from posted journal entries for all time.</p>
      <LoadState loading={loading} error={error} empty={data?.rows.length === 0} emptyText="No posted balances yet." />
      {data && data.rows.length > 0 && (
        <>
          {!data.balanced && <p className="setup-error" role="alert">The trial balance does not balance. Do not rely on these figures until the ledger is investigated.</p>}
          {data.balanced && <p className="enrol-success" role="status">Trial balance is balanced.</p>}
          <table className="fin-table">
            <thead><tr><th>Account</th><th>Type</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead>
            <tbody>{data.rows.map((row) => (
              <tr key={row.account}><td>{row.account}</td><td>{row.type}</td><td className="num">{row.debit === "0.00" ? "—" : formatMoney(row.debit)}</td><td className="num">{row.credit === "0.00" ? "—" : formatMoney(row.credit)}</td></tr>
            ))}</tbody>
            <tfoot><tr className="strong"><td colSpan={2}>Totals</td><td className="num">{formatMoney(data.total_debit)}</td><td className="num">{formatMoney(data.total_credit)}</td></tr></tfoot>
          </table>
        </>
      )}
    </section>
  );
}

export default function FinanceReportsPage() {
  return (
    <FinanceShell eyebrow="REPORTING" title="Reports" active="/finance/reports" description="Review ledger balances and confirm that all posted entries remain in balance.">
      {(ctx: FinanceContext) => <TrialBalanceReport ctx={ctx} />}
    </FinanceShell>
  );
}

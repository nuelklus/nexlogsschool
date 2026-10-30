"use client";

import { ReminderCandidate } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, Money, useFinanceData } from "./finance-shell";

function ReminderReview({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading } = useFinanceData<ReminderCandidate[]>(ctx, "reminders/");
  return (
    <section className="enrol-card roster" aria-label="Overdue fee reminder candidates">
      <div className="enrol-card-head"><div><p className="eyebrow">FOLLOW-UP</p><h2>Overdue accounts</h2></div></div>
      <p className="fin-note">Review these accounts before contacting families. No SMS or WhatsApp provider is configured, so this page will not send messages.</p>
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No overdue balances need follow-up." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Student</th><th>Class</th><th>Oldest due</th><th>Open invoices</th><th className="num">Overdue</th><th>Guardian contacts</th></tr></thead>
          <tbody>{data.map((row) => (
            <tr key={row.student}>
              <td>{row.student_name}<small>{row.admission_number}</small></td>
              <td>{row.grade_level || "—"}</td>
              <td>{row.oldest_due_date}</td>
              <td>{row.invoices}</td>
              <td className="num"><Money value={row.overdue_balance} /></td>
              <td>{row.phones.length ? `${row.phones.length} on file` : "No phone on file"}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </section>
  );
}

export default function FinanceRemindersPage() {
  return (
    <FinanceShell eyebrow="COLLECTIONS" title="Reminders" active="/finance/reminders" description="Review overdue balances and guardian contact availability.">
      {(ctx: FinanceContext) => <ReminderReview ctx={ctx} />}
    </FinanceShell>
  );
}

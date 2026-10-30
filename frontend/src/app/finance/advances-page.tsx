"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { GRADE_LEVELS } from "../grade-levels";
import { AdvanceRow, financeRequest, formatMoney, newIdempotencyKey } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");

function Advances({ ctx }: { ctx: FinanceContext }) {
  const [grade, setGrade] = useState("");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState("");
  const { data, error, loading, reload } = useFinanceData<AdvanceRow[]>(ctx, `reports/advances/${grade ? `?${new URLSearchParams({ grade_level: grade })}` : ""}`);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data ?? []).filter((row) => !needle || `${row.student_name} ${row.admission_number}`.toLowerCase().includes(needle));
  }, [data, query]);

  const total = useMemo(() => visible.reduce((sum, row) => sum + Math.round(Number(row.advance) * 100), 0) / 100, [visible]);

  async function apply(row: AdvanceRow) {
    if (!window.confirm(`Apply ${row.student_name}'s advance to their oldest outstanding invoices?`)) return;
    setBusy(row.student);
    setMessage("");
    setFailed(false);
    try {
      for (const payment of row.payments) {
        await financeRequest(ctx.authFetch, ctx.tenantId, `payments/${payment.payment}/allocate/`, {
          method: "POST",
          body: { auto_allocate: true },
          idempotencyKey: newIdempotencyKey(),
        });
      }
      setMessage(`Advance applied for ${row.student_name}.`);
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
      reload();
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="enrol-card roster" aria-label="Advances">
      <div className="enrol-card-head">
        <div><p className="eyebrow">ADVANCE CREDIT</p><h2>Students holding an advance</h2></div>
        <div className="fin-inline-controls">
          <select aria-label="Filter by class" value={grade} onChange={(event) => setGrade(event.target.value)}>
            <option value="">All classes</option>
            {GRADE_LEVELS.map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
          <input className="roster-search" type="search" placeholder="Search student" aria-label="Search students" value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
      </div>
      <p className="fin-note">Money received that has not been applied to an invoice or refunded. Applying it pays the student&apos;s oldest outstanding invoices first.</p>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No student is holding an advance." />
      {visible.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Student</th><th>Class</th><th className="num">Advance</th><th className="num">Owes now</th><th /></tr></thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.student}>
                <td>{row.student_name}<small>{row.admission_number}</small></td>
                <td>{row.grade_level || "—"}</td>
                <td className="num">{formatMoney(row.advance)}</td>
                <td className="num">{formatMoney(row.open_balance)}</td>
                <td className="fin-actions">
                  <Link href={`/finance/statements/${row.student}`}>Statement</Link>
                  {Number(row.open_balance) > 0 && (
                    <button type="button" className="link-button" disabled={busy === row.student} onClick={() => void apply(row)}>
                      {busy === row.student ? "Applying…" : "Apply to invoices"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr className="strong"><td colSpan={2}>Total held</td><td className="num">{formatMoney(total.toFixed(2))}</td><td colSpan={2} /></tr></tfoot>
        </table>
      )}
    </section>
  );
}

export default function AdvancesPage() {
  return (
    <FinanceShell
      eyebrow="COLLECTIONS"
      title="Advances"
      active="/finance/advances"
      description="Students who have paid more than they owe, and how much credit the school holds for each."
    >
      {(ctx) => <Advances ctx={ctx} />}
    </FinanceShell>
  );
}

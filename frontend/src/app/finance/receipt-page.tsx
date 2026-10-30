"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { Receipt, fetchPdf, formatMoney } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

function ReceiptView({ ctx, id }: { ctx: FinanceContext; id: string }) {
  const { data, error, loading } = useFinanceData<Receipt>(ctx, `payments/${id}/receipt/`);
  const [message, setMessage] = useState("");

  async function openPdf() {
    setMessage("");
    try {
      const blob = await fetchPdf(ctx.authFetch, ctx.tenantId, id);
      window.open(URL.createObjectURL(blob), "_blank", "noopener");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "The PDF could not be loaded.");
    }
  }

  if (!data) return <LoadState loading={loading} error={error} />;
  return (
    <article className="enrol-card print-sheet receipt" aria-label="Receipt">
      <header className="receipt-head">
        <h2>{data.school}</h2>
        <p>OFFICIAL RECEIPT{data.status === "reversed" ? " — REVERSED" : ""}</p>
      </header>
      <dl className="fin-facts">
        <div><dt>Receipt no.</dt><dd>{data.receipt_number}</dd></div>
        <div><dt>Date</dt><dd>{data.date}</dd></div>
        <div><dt>Student</dt><dd>{data.student.name}</dd></div>
        <div><dt>Admission no.</dt><dd>{data.student.admission_number}</dd></div>
        <div><dt>Class</dt><dd>{data.student.grade_level || "—"}</dd></div>
        <div><dt>Method</dt><dd>{data.method}{data.reference ? ` · ${data.reference}` : ""}</dd></div>
        {data.payer_name && <div><dt>Received from</dt><dd>{data.payer_name}</dd></div>}
      </dl>
      <table className="fin-table">
        <thead><tr><th>Applied to</th><th className="num">Amount</th></tr></thead>
        <tbody>
          {data.allocations.map((a) => <tr key={a.invoice}><td>Invoice {a.invoice}</td><td className="num">{formatMoney(a.amount)}</td></tr>)}
          {data.advance_credit !== "0.00" && <tr><td>Advance credit</td><td className="num">{formatMoney(data.advance_credit)}</td></tr>}
        </tbody>
        <tfoot><tr className="strong"><td>Amount received</td><td className="num">{formatMoney(data.amount)}</td></tr></tfoot>
      </table>
      {message && <p className="setup-error" role="alert">{message}</p>}
      <div className="enrol-actions no-print">
        <button type="button" className="setup-submit" onClick={() => window.print()}>Print receipt</button>
        <button type="button" className="auth-signout" onClick={() => void openPdf()}>Open PDF</button>
      </div>
    </article>
  );
}

export default function ReceiptPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <FinanceShell eyebrow="PAYMENTS" title="Receipt" active="/finance/payments" description="Print this receipt or open the PDF copy.">
      {(ctx) => <ReceiptView ctx={ctx} id={id} />}
    </FinanceShell>
  );
}

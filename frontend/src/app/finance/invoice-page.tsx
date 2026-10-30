"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useState } from "react";

import { Invoice, financeRequest, formatMoney, newIdempotencyKey } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");

function Detail({ ctx, id }: { ctx: FinanceContext; id: string }) {
  const { data: invoice, error, loading, reload } = useFinanceData<Invoice>(ctx, `invoices/${id}/`);
  const [message, setMessage] = useState("");
  const [creditKey, setCreditKey] = useState(newIdempotencyKey);

  async function act(path: string, body?: unknown, key?: string) {
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, path, { method: "POST", body, idempotencyKey: key });
      reload();
      return true;
    } catch (caught) {
      setMessage(errorText(caught));
      return false;
    }
  }

  async function credit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const saved = await act(
      `invoices/${id}/credit-notes/`,
      { amount: String(values.get("amount") ?? "").trim(), reason_type: values.get("reason_type"), reason: String(values.get("reason") ?? "").trim() },
      creditKey,
    );
    if (saved) {
      form.reset();
      setCreditKey(newIdempotencyKey());
    }
  }

  function voidInvoice() {
    const reason = window.prompt("Reason for voiding this invoice?");
    if (reason?.trim()) void act(`invoices/${id}/void/`, { reason: reason.trim() });
  }

  if (!invoice) return <LoadState loading={loading} error={error} />;
  const open = invoice.status === "issued" || invoice.status === "partially_paid";
  return (
    <article className="enrol-card print-sheet" aria-label="Invoice">
      <div className="enrol-card-head">
        <div>
          <p className="eyebrow">INVOICE</p>
          <h2>{invoice.number || "Draft invoice"}</h2>
          <p className="fin-note">{invoice.student_name} · {invoice.admission_number} · {invoice.grade_level}</p>
        </div>
        <StatusPill status={invoice.overdue ? "overdue" : invoice.status} />
      </div>
      <dl className="fin-facts">
        <div><dt>Academic year</dt><dd>{invoice.academic_year_name}</dd></div>
        <div><dt>Period</dt><dd>{invoice.period_label || "—"}</dd></div>
        <div><dt>Issued</dt><dd>{invoice.issue_date ?? "Not issued"}</dd></div>
        <div><dt>Due</dt><dd>{invoice.due_date}</dd></div>
      </dl>
      <table className="fin-table">
        <thead><tr><th>Description</th><th className="num">Amount</th></tr></thead>
        <tbody>
          {invoice.lines?.map((line) => (
            <tr key={line.id}><td>{line.description}{line.kind === "discount" ? " (discount)" : ""}</td><td className="num">{line.kind === "discount" ? "−" : ""}{formatMoney(line.amount)}</td></tr>
          ))}
        </tbody>
        <tfoot>
          <tr><td>Total</td><td className="num">{formatMoney(invoice.total)}</td></tr>
          <tr><td>Paid</td><td className="num">{formatMoney(invoice.amount_paid)}</td></tr>
          <tr><td>Credited</td><td className="num">{formatMoney(invoice.credited_total)}</td></tr>
          <tr className="strong"><td>Balance due</td><td className="num">{formatMoney(invoice.balance)}</td></tr>
        </tfoot>
      </table>
      {invoice.calculation && invoice.calculation.excluded.length > 0 && (
        <section aria-label="Fees not charged" className="no-print">
          <h3>Fees not charged on this invoice</h3>
          <ul className="fin-chips">
            {invoice.calculation.excluded.map((item) => <li key={item.description + item.code}>{item.description}: {item.reason}</li>)}
          </ul>
        </section>
      )}
      {invoice.payments && invoice.payments.length > 0 && (
        <section aria-label="Payments applied">
          <h3>Payments applied</h3>
          <ul className="fin-list">
            {invoice.payments.map((p) => (
              <li key={`${p.payment}-${p.amount}`}><Link href={`/finance/receipts/${p.payment}`}>{p.receipt_number}</Link> · {p.date} · {formatMoney(p.amount)}</li>
            ))}
          </ul>
        </section>
      )}
      {invoice.credit_notes && invoice.credit_notes.length > 0 && (
        <section aria-label="Credit notes">
          <h3>Credit notes</h3>
          <ul className="fin-list">
            {invoice.credit_notes.map((n) => <li key={n.number}>{n.number} · {formatMoney(n.amount)} · {n.reason}</li>)}
          </ul>
        </section>
      )}
      {message && <p className="setup-error" role="alert">{message}</p>}
      <div className="enrol-actions no-print">
        <Link className="auth-signout" href={`/finance/statements/${invoice.student}`}>Student statement</Link>
        <button type="button" className="auth-signout" onClick={() => window.print()}>Print</button>
        {invoice.status === "draft" && <button type="button" className="setup-submit" onClick={() => void act(`invoices/${id}/issue/`)}>Issue invoice</button>}
        {ctx.canApprove && invoice.status !== "voided" && invoice.amount_paid === "0.00" && invoice.credited_total === "0.00" && (
          <button type="button" className="auth-signout" onClick={voidInvoice}>Void invoice</button>
        )}
      </div>
      {ctx.canApprove && open && (
        <form className="fin-inline no-print" onSubmit={credit} aria-label="Raise credit note">
          <label>Credit amount (GHS)<input name="amount" inputMode="decimal" required /></label>
          <label>Type
            <select name="reason_type" defaultValue="correction">
              <option value="correction">Correction</option>
              <option value="bad_debt">Bad debt write-off</option>
            </select>
          </label>
          <label>Reason<input name="reason" maxLength={240} required /></label>
          <button className="setup-submit" type="submit">Raise credit note</button>
        </form>
      )}
    </article>
  );
}

export default function InvoicePage() {
  const { id } = useParams<{ id: string }>();
  return (
    <FinanceShell eyebrow="BILLING" title="Invoice" active="/finance/invoices" description="Invoice lines are a snapshot taken when the draft was created.">
      {(ctx) => <Detail ctx={ctx} id={id} />}
    </FinanceShell>
  );
}

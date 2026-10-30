"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { Payment, PaymentMethod, financeRequest, formatMoney, newIdempotencyKey } from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";
import { useStudents } from "./billing-page";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function RecordForm({ ctx, onDone }: { ctx: FinanceContext; onDone: () => void }) {
  const students = useStudents(ctx);
  const methods = useFinanceData<PaymentMethod[]>(ctx, "payment-methods/");
  const [kind, setKind] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const needsReference = kind !== "" && kind !== "cash";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      const payment = await financeRequest<Payment>(ctx.authFetch, ctx.tenantId, "payments/", {
        method: "POST",
        idempotencyKey: key,
        body: {
          student: text(values, "student"),
          method: text(values, "method"),
          amount: text(values, "amount"),
          payment_date: text(values, "payment_date") || undefined,
          reference: text(values, "reference"),
          payer_name: text(values, "payer_name"),
          auto_allocate: values.get("auto_allocate") === "on",
        },
      });
      setFailed(false);
      setMessage(
        payment.status === "pending"
          ? `Recorded as pending (${formatMoney(payment.amount)}). It counts only after you confirm the mobile-money transfer.`
          : `Payment confirmed. Receipt ${payment.receipt_number}.`,
      );
      setKey(newIdempotencyKey());
      form.reset();
      setKind("");
      onDone();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="enrol-card" onSubmit={submit} aria-label="Record payment">
      <div className="enrol-card-head"><div><p className="eyebrow">NEW PAYMENT</p><h2>Record a payment</h2></div></div>
      <div className="enrol-grid">
        <label className="enrol-wide">Student
          <select name="student" required defaultValue="">
            <option value="" disabled>Select student</option>
            {students.map((s) => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} · {s.admission_number}</option>)}
          </select>
        </label>
        <label>Method
          <select name="method" required defaultValue="" onChange={(event) => setKind(methods.data?.find((m) => m.id === event.target.value)?.kind ?? "")}>
            <option value="" disabled>Select method</option>
            {methods.data?.filter((m) => m.is_active).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        <label>Amount (GHS)<input name="amount" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required placeholder="0.00" /></label>
        <label>Payment date<input name="payment_date" type="date" /></label>
        <label>{kind === "mobile_money" ? "Transaction ID" : kind === "cheque" ? "Cheque number" : "Reference"}
          <input name="reference" maxLength={80} required={needsReference} />
        </label>
        <label className="enrol-wide">Paid by (optional)<input name="payer_name" maxLength={120} /></label>
        <label className="fin-check enrol-wide"><input name="auto_allocate" type="checkbox" defaultChecked /> Apply to the oldest outstanding invoices first. Any extra is held as an advance.</label>
      </div>
      {kind === "mobile_money" && <p className="fin-note">Mobile-money payments stay pending until you confirm the money has arrived.</p>}
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      <div className="enrol-actions"><button className="setup-submit" type="submit" disabled={busy}>{busy ? "Saving…" : "Record payment"}</button></div>
    </form>
  );
}

function History({ ctx, refresh }: { ctx: FinanceContext; refresh: number }) {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useFinanceData<Payment[]>(ctx, `payments/?${new URLSearchParams({ ...(status ? { status } : {}), r: String(refresh) })}`);
  const [message, setMessage] = useState("");

  async function act(path: string, body?: unknown) {
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, path, { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      reload();
    } catch (caught) {
      setMessage(errorText(caught));
    }
  }

  function withReason(payment: Payment, action: "reverse" | "fail") {
    const reason = window.prompt(action === "reverse" ? "Reason for reversing this payment?" : "Why did this payment fail?");
    if (reason?.trim()) void act(`payments/${payment.id}/${action}/`, { reason: reason.trim() });
  }

  function refund(payment: Payment) {
    const amount = window.prompt(`Refund amount (up to ${formatMoney(payment.unallocated)}, GHS)?`);
    const reason = amount ? window.prompt("Reason for the refund?") : null;
    if (amount?.trim() && reason?.trim()) void act(`payments/${payment.id}/refund/`, { amount: amount.trim(), reason: reason.trim() });
  }

  return (
    <section className="enrol-card roster" aria-label="Payment history">
      <div className="enrol-card-head">
        <div><p className="eyebrow">HISTORY</p><h2>Payments</h2></div>
        <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {["pending", "confirmed", "reversed", "failed"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {message && <p className="setup-error" role="alert">{message}</p>}
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No payments recorded yet." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Date</th><th>Student</th><th>Method</th><th className="num">Amount</th><th className="num">Advance</th><th>Status</th><th /></tr></thead>
          <tbody>
            {data.map((payment) => (
              <tr key={payment.id}>
                <td>{payment.payment_date}</td>
                <td>{payment.student_name}<small>{payment.receipt_number || payment.reference}</small></td>
                <td>{payment.method_name}</td>
                <td className="num">{formatMoney(payment.amount)}</td>
                <td className="num">{formatMoney(payment.unallocated)}</td>
                <td><StatusPill status={payment.status} /></td>
                <td className="fin-actions">
                  {payment.status === "pending" && (
                    <>
                      <button type="button" className="link-button" onClick={() => void act(`payments/${payment.id}/confirm/`, { auto_allocate: true })}>Confirm</button>
                      <button type="button" className="link-button" onClick={() => withReason(payment, "fail")}>Mark failed</button>
                    </>
                  )}
                  {payment.status === "confirmed" && <Link href={`/finance/receipts/${payment.id}`}>Receipt</Link>}
                  {payment.status === "confirmed" && ctx.canApprove && (
                    <>
                      {payment.refunded_total === "0.00" && <button type="button" className="link-button" onClick={() => withReason(payment, "reverse")}>Reverse</button>}
                      {payment.unallocated !== "0.00" && <button type="button" className="link-button" onClick={() => refund(payment)}>Refund</button>}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Payments({ ctx }: { ctx: FinanceContext }) {
  const [refresh, setRefresh] = useState(0);
  return (
    <div className="enrol-layout">
      <RecordForm ctx={ctx} onDone={() => setRefresh((value) => value + 1)} />
      <History ctx={ctx} refresh={refresh} />
    </div>
  );
}

export default function PaymentsPage() {
  return (
    <FinanceShell
      eyebrow="PAYMENTS"
      title="Payments"
      active="/finance/payments"
      description="Record fees received. Cash, bank and cheque payments are confirmed straight away; mobile money waits for your confirmation."
    >
      {(ctx) => <Payments ctx={ctx} />}
    </FinanceShell>
  );
}

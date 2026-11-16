"use client";

import { useState } from "react";

import { formatMoney } from "./finance-api";
import { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

type Snapshot = { id: string; name: string };
type InvoiceReview = {
  id: string;
  number: string;
  status: string;
  student: string;
  admission_number: string;
  academic_year: string;
  period: string;
  issue_date: string | null;
  class_snapshot: string;
  campus_snapshot: Snapshot | null;
  current_student_class_context: string;
  current_student_campus_context: string | null;
  total: string;
  amount_paid: string;
  issues: string[];
};
type PaymentAllocationReview = {
  invoice: string;
  invoice_number: string;
  class_snapshot: string;
  campus_snapshot: Snapshot | null;
  amount: string;
  issues: string[];
};
type PaymentReview = {
  id: string;
  receipt_number: string;
  payment_date: string;
  student: string;
  admission_number: string;
  method: string;
  amount: string;
  allocated_total: string;
  allocation_records_total: string;
  refunded_total: string;
  unattributed_amount: string;
  issues: string[];
  allocations: PaymentAllocationReview[];
};
type Reconciliation = {
  read_only: true;
  basis: string;
  pagination: { limit: number; offset: number; invoice_total: number; payment_total: number };
  invoice_issues: InvoiceReview[];
  payment_issues: PaymentReview[];
};

const issueLabels: Record<string, string> = {
  invoice_missing_class_snapshot: "Invoice has no class snapshot",
  invoice_missing_campus_snapshot: "Invoice has no campus snapshot",
  invoice_campus_tenant_mismatch: "Invoice campus belongs to another school",
  invoice_student_tenant_mismatch: "Invoice student belongs to another school",
  invoice_academic_year_tenant_mismatch: "Invoice academic year belongs to another school",
  payment_has_unattributed_amount: "Confirmed amount is not allocated or refunded",
  payment_allocation_total_mismatch: "Stored allocation total differs from allocation records",
  payment_student_tenant_mismatch: "Payment student belongs to another school",
  payment_method_tenant_mismatch: "Payment method belongs to another school",
  allocation_tenant_mismatch: "Allocation references another school's record",
  allocation_student_mismatch: "Allocation invoice belongs to another student",
};

function Issues({ issues }: { issues: string[] }) {
  return <ul>{issues.map((issue) => <li key={issue}>{issueLabels[issue] ?? issue.replaceAll("_", " ")}</li>)}</ul>;
}

export default function LegacyAttributionReconciliation({ ctx }: { ctx: FinanceContext }) {
  const limit = 50;
  const [offset, setOffset] = useState(0);
  const path = ctx.canApprove ? `reports/legacy-attribution-reconciliation/?limit=${limit}&offset=${offset}` : null;
  const { data, error, loading, reload } = useFinanceData<Reconciliation>(ctx, path);

  function refresh() {
    if (offset > 0) setOffset(0);
    else reload();
  }

  if (!ctx.canApprove) {
    return <section className="enrol-card" aria-label="Legacy financial reconciliation">
      <h2>Legacy financial attribution</h2>
      <p className="fin-note">This school-wide reconciliation is available to owners and school administrators. Ask one of them to review legacy invoice and payment attribution.</p>
    </section>;
  }

  return (
    <section className="enrol-card roster" aria-label="Legacy financial reconciliation">
      <div className="enrol-card-head">
        <div><p className="eyebrow">DATA REVIEW · READ ONLY</p><h2>Legacy invoice and payment attribution</h2></div>
        <button type="button" className="secondary-action" onClick={refresh}>Refresh report</button>
      </div>
      <p className="fin-note">This report makes no changes. Missing snapshots and unallocated money are review signals, not proof of an error. Verify records against original school documents before making any correction.</p>
      <LoadState loading={loading} error={error} />
      {data && <>
        <p className="fin-note">{data.basis}</p>
        <p role="status">
          {data.pagination.invoice_total} invoice(s) and {data.pagination.payment_total} payment(s) flagged for review.
        </p>
        <h3>Invoices ({data.pagination.invoice_total})</h3>
        {data.invoice_issues.length === 0 ? <p>No invoice attribution issues on this page.</p> : (
          <div className="fin-stack">{data.invoice_issues.map((invoice) => (
            <article className="academic-card" key={invoice.id}>
              <strong>{invoice.number || "Unnumbered invoice"} · {invoice.student} ({invoice.admission_number})</strong>
              <p>{invoice.status} · {invoice.academic_year} · {invoice.period || "No term"} · {invoice.issue_date || "Not issued"}</p>
              <p>Stored snapshot: {invoice.class_snapshot || "No class"} · {invoice.campus_snapshot?.name ?? "No valid campus"} · {formatMoney(invoice.total)} total · {formatMoney(invoice.amount_paid)} paid</p>
              <p>Current student details for comparison only: {invoice.current_student_class_context || "No class"} · campus ID {invoice.current_student_campus_context ?? "Not set"}</p>
              <Issues issues={invoice.issues} />
            </article>
          ))}</div>
        )}
        <h3>Confirmed payments ({data.pagination.payment_total})</h3>
        {data.payment_issues.length === 0 ? <p>No payment attribution issues on this page.</p> : (
          <div className="fin-stack">{data.payment_issues.map((payment) => (
            <article className="academic-card" key={payment.id}>
              <strong>{payment.receipt_number || "No receipt"} · {payment.student} ({payment.admission_number})</strong>
              <p>{payment.payment_date} · {payment.method} · {formatMoney(payment.amount)} received · {formatMoney(payment.unattributed_amount)} unattributed</p>
              <p>Stored allocated: {formatMoney(payment.allocated_total)} · Allocation records: {formatMoney(payment.allocation_records_total)} · Refunded: {formatMoney(payment.refunded_total)}</p>
              <Issues issues={payment.issues} />
              {payment.allocations.length > 0 && <ul>{payment.allocations.map((allocation) => (
                <li key={allocation.invoice}>
                  {allocation.invoice_number || "Unnumbered invoice"} · {allocation.class_snapshot || "No class snapshot"} · {allocation.campus_snapshot?.name ?? "No valid campus"} · {formatMoney(allocation.amount)}
                  {allocation.issues.length > 0 && <Issues issues={allocation.issues} />}
                </li>
              ))}</ul>}
            </article>
          ))}</div>
        )}
        {(offset > 0 || offset + limit < Math.max(data.pagination.invoice_total, data.pagination.payment_total)) && (
          <div className="enrol-actions">
            {offset > 0 && <button type="button" className="secondary-action" onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button>}
            {offset + limit < Math.max(data.pagination.invoice_total, data.pagination.payment_total) && (
              <button type="button" className="secondary-action" onClick={() => setOffset(offset + limit)}>Next</button>
            )}
          </div>
        )}
      </>}
    </section>
  );
}

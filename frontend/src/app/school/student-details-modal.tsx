"use client";

import { useEffect, useRef, useState } from "react";
import { formatMoney, statusLabel } from "../finance/finance-api";
import { requestError, type AuthFetch, type Campus, type Student } from "./student-api";
import type { SchoolClass } from "../academics/academics-api";

type Invoice = {
  id: string; student: string; number: string; status: string; academic_year_name: string;
  period_label: string; issue_date: string | null; due_date: string; total: string;
  amount_paid: string; credited_total: string; balance: string; brought_forward: string;
  total_due: string; lines: { id: string; description: string; kind: string; amount: string }[];
};
type Statement = { outstanding_invoices: string; advance_credit: string };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const money = (value: unknown): value is string => typeof value === "string" && /^\d+(\.\d{1,2})?$/.test(value);

async function get(authFetch: AuthFetch, path: string): Promise<unknown> {
  const response = await authFetch(path);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload;
}

function isInvoice(value: unknown): value is Invoice {
  return record(value) && ["id", "student", "number", "status", "academic_year_name", "period_label"].every((key) => typeof value[key] === "string")
    && (value.issue_date === null || typeof value.issue_date === "string") && typeof value.due_date === "string"
    && money(value.total) && money(value.amount_paid) && money(value.credited_total) && money(value.balance) && money(value.brought_forward) && money(value.total_due)
    && Array.isArray(value.lines) && value.lines.every((line: unknown) => record(line)
      && typeof line.id === "string" && typeof line.description === "string" && typeof line.kind === "string" && money(line.amount));
}

export default function StudentDetailsModal({ student, schoolClass, campus, authFetch, tenantId, onClose, returnFocus }: {
  student: Student; schoolClass: SchoolClass | null; campus: Campus | null;
  authFetch: AuthFetch; tenantId: string; onClose: () => void; returnFocus?: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [invoiceLoading, setInvoiceLoading] = useState(true);
  const [invoiceError, setInvoiceError] = useState("");
  const [statement, setStatement] = useState<Statement | null>(null);
  const [statementError, setStatementError] = useState("");
  const [statementLoading, setStatementLoading] = useState(true);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => {
      if (element?.open) element.close();
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [returnFocus]);

  useEffect(() => {
    let active = true;
    const base = `/tenants/${tenantId}/finance/`;
    async function loadInvoice() {
      try {
        const list = await get(authFetch, `${base}invoices/?student=${encodeURIComponent(student.id)}`);
        if (!Array.isArray(list) || !list.every((item: unknown) => record(item)
          && typeof item.id === "string" && typeof item.status === "string" && item.student === student.id)) {
          throw new Error("Unexpected invoice list response.");
        }
        const latest = list.find((item) => item.status !== "voided");
        if (latest) {
          const detail = await get(authFetch, `${base}invoices/${encodeURIComponent(latest.id)}/`);
          if (!isInvoice(detail) || detail.student !== student.id || detail.id !== latest.id) throw new Error("Unexpected invoice details response.");
          if (active) setInvoice(detail);
        }
      } catch (caught) {
        if (active) setInvoiceError(caught instanceof Error ? caught.message : "Could not load invoices.");
      } finally {
        if (active) setInvoiceLoading(false);
      }
    }
    async function loadStatement() {
      try {
        const data = await get(authFetch, `${base}students/${encodeURIComponent(student.id)}/statement/`);
        if (!record(data) || !money(data.outstanding_invoices) || !money(data.advance_credit)) {
          throw new Error("Unexpected account summary response.");
        }
        if (active) setStatement({ outstanding_invoices: data.outstanding_invoices, advance_credit: data.advance_credit });
      } catch (caught) {
        if (active) setStatementError(caught instanceof Error ? caught.message : "Could not load the account summary.");
      } finally {
        if (active) setStatementLoading(false);
      }
    }
    void loadInvoice();
    void loadStatement();
    return () => { active = false; };
  }, [authFetch, tenantId, student.id]);

  return <dialog ref={dialog} className="student-dialog" aria-labelledby="student-details-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="student-dialog-header">
      <div className="student-dialog-identity">
        <span className="student-dialog-avatar" aria-hidden="true">{`${student.first_name[0] ?? ""}${student.last_name[0] ?? ""}`.toUpperCase()}</span>
        <div><p className="eyebrow">STUDENT DETAILS</p><h2 id="student-details-title">{student.first_name} {student.last_name}</h2><p className="student-dialog-subtitle">{student.admission_number}</p></div>
      </div>
      <button type="button" className="student-dialog-close" onClick={onClose} autoFocus>Close</button>
    </div>
    <section aria-label="Student profile">
      <h3>Student and class</h3>
      <dl className="fin-facts">
        <div><dt>Admission number</dt><dd>{student.admission_number}</dd></div>
        <div><dt>Status</dt><dd>{statusLabel(student.status)}</dd></div>
        <div><dt>Current-year class</dt><dd>{schoolClass ? [schoolClass.name, schoolClass.section].filter(Boolean).join(" ") : student.current_class ? "Class details unavailable" : "No unique current class"}</dd></div>
        {schoolClass?.room && <div><dt>Class room</dt><dd>{schoolClass.room}</dd></div>}
        <div><dt>Campus</dt><dd>{campus?.name ?? (student.campus ? "Campus details unavailable" : "Not assigned")}</dd></div>
        <div><dt>Enrollment date</dt><dd>{student.enrolled_on}</dd></div>
        <div><dt>Date of birth</dt><dd>{student.date_of_birth ?? "Not provided"}</dd></div>
        <div><dt>Gender</dt><dd>{student.gender || "Not specified"}</dd></div>
        <div><dt>Portal account</dt><dd>{student.portal_account ? "Active" : "Not active"}</dd></div>
      </dl>
    </section>
    <section aria-label="Student account">
      <h3>Account summary</h3>
      {statementLoading && <p role="status">Loading account summary…</p>}
      {statementError && <p role="alert" className="setup-error">Account summary: {statementError}</p>}
      {statement && <><dl className="fin-facts">
        <div><dt>Outstanding invoices</dt><dd>{formatMoney(statement.outstanding_invoices)}</dd></div>
        <div><dt>Unallocated advance credit</dt><dd>{formatMoney(statement.advance_credit)}</dd></div>
      </dl><p className="fin-note">Outstanding invoices and unallocated advance credit are shown separately.</p></>}
    </section>
    <section aria-label="Latest invoice">
      <h3>Latest invoice</h3>
      <p className="fin-note">Most recently created non-void invoice; it may not be for the current term.</p>
      {invoiceLoading && <p role="status">Loading invoice…</p>}
      {invoiceError && <p role="alert" className="setup-error">Invoice: {invoiceError}</p>}
      {!invoiceLoading && !invoiceError && !invoice && <p>No non-void invoices found among the 500 most recently created invoices for this student.</p>}
      {invoice && <>
        <p><strong>{invoice.number || "Draft invoice"}</strong> · {statusLabel(invoice.status)} · {invoice.academic_year_name} · {invoice.period_label || "No period"}</p>
        <ul className="student-dialog-invoice-lines">{invoice.lines.map((line) => <li key={line.id}><span>{line.description}{line.kind === "discount" ? " (discount)" : ""}: </span><strong>{line.kind === "discount" ? "−" : ""}{formatMoney(line.amount)}</strong></li>)}</ul>
        <dl className="fin-facts">
          <div><dt>Invoice total</dt><dd>{formatMoney(invoice.total)}</dd></div>
          {invoice.status !== "draft" && <>
            <div><dt>Issued</dt><dd>{invoice.issue_date ?? "Not available"}</dd></div>
            <div><dt>Due date</dt><dd>{invoice.due_date}</dd></div>
            <div><dt>Paid on this invoice</dt><dd>{formatMoney(invoice.amount_paid)}</dd></div>
            <div><dt>Credits on this invoice</dt><dd>{formatMoney(invoice.credited_total)}</dd></div>
            <div><dt>This invoice balance</dt><dd>{formatMoney(invoice.balance)}</dd></div>
            <div><dt>Earlier invoices brought forward</dt><dd>{formatMoney(invoice.brought_forward)}</dd></div>
            <div><dt>Invoice and earlier balance</dt><dd>{formatMoney(invoice.total_due)}</dd></div>
          </>}
        </dl>
        {invoice.status === "draft" && <p className="fin-note">Draft amounts are not yet issued or included in outstanding invoices.</p>}
      </>}
    </section>
  </dialog>;
}

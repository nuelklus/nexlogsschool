"use client";

import { FormEvent, useState } from "react";

import {
  AccountingPeriod,
  FinanceSettings,
  LedgerAccount,
  PaymentMethod,
  financeRequest,
} from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "./finance-shell";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const formText = (form: FormData, key: string) => String(form.get(key) ?? "").trim();

function SettingsForm({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading, reload } = useFinanceData<FinanceSettings>(ctx, "settings/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "settings/", {
        method: "PATCH",
        body: {
          invoice_prefix: formText(values, "invoice_prefix"),
          receipt_prefix: formText(values, "receipt_prefix"),
          credit_note_prefix: formText(values, "credit_note_prefix"),
          default_payment_terms_days: formText(values, "default_payment_terms_days"),
        },
      });
      setFailed(false);
      setMessage("Finance settings saved.");
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <section className="enrol-card" aria-label="Finance settings">
      <div className="enrol-card-head"><div><p className="eyebrow">DOCUMENTS</p><h2>Finance settings</h2></div></div>
      <LoadState loading={loading} error={error} />
      {data && (
        <form className="enrol-grid" onSubmit={save}>
          <label>Currency<input value={data.currency} readOnly /></label>
          <label>Invoice prefix<input name="invoice_prefix" defaultValue={data.invoice_prefix} maxLength={10} required /></label>
          <label>Receipt prefix<input name="receipt_prefix" defaultValue={data.receipt_prefix} maxLength={10} required /></label>
          <label>Credit note prefix<input name="credit_note_prefix" defaultValue={data.credit_note_prefix} maxLength={10} required /></label>
          <label>Default payment terms (days)<input name="default_payment_terms_days" type="number" min="0" max="365" defaultValue={data.default_payment_terms_days} required /></label>
          <div className="enrol-wide">
            {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
            {ctx.canApprove && <button className="setup-submit" type="submit">Save settings</button>}
          </div>
        </form>
      )}
    </section>
  );
}

function AccountSetup({ ctx }: { ctx: FinanceContext }) {
  const accounts = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const methods = useFinanceData<PaymentMethod[]>(ctx, "payment-methods/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function addAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "accounts/", {
        method: "POST",
        body: { code: formText(values, "code"), name: formText(values, "name"), type: formText(values, "type") },
      });
      form.reset();
      accounts.reload();
      setFailed(false);
      setMessage("Ledger account added.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function addMethod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "payment-methods/", {
        method: "POST",
        body: { name: formText(values, "method_name"), kind: formText(values, "kind"), account: formText(values, "account") },
      });
      form.reset();
      methods.reload();
      setFailed(false);
      setMessage("Payment method added. Online payments are not connected; payments remain staff-recorded.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fin-grid">
      <section className="enrol-card" aria-label="Chart of accounts">
        <div className="enrol-card-head"><div><p className="eyebrow">LEDGER</p><h2>Chart of accounts</h2></div></div>
        <LoadState loading={accounts.loading} error={accounts.error} empty={accounts.data?.length === 0} emptyText="No accounts configured." />
        {accounts.data && accounts.data.length > 0 && (
          <table className="fin-table">
            <thead><tr><th>Code</th><th>Account</th><th>Type</th><th>Status</th></tr></thead>
            <tbody>{accounts.data.map((account) => (
              <tr key={account.id}><td>{account.code}</td><td>{account.name}{account.system_key && <small>System account</small>}</td><td>{account.type}</td><td>{account.is_active ? "Active" : "Inactive"}</td></tr>
            ))}</tbody>
          </table>
        )}
        {ctx.canApprove && (
          <form className="enrol-grid" onSubmit={addAccount} aria-label="Add ledger account">
            <label>Code<input name="code" maxLength={20} required /></label>
            <label>Name<input name="name" maxLength={160} required /></label>
            <label>Type<select name="type" required defaultValue=""><option value="" disabled>Select type</option>{["asset", "liability", "equity", "income", "expense"].map((type) => <option key={type}>{type}</option>)}</select></label>
            <button className="setup-submit" type="submit" disabled={busy}>Add account</button>
          </form>
        )}
      </section>
      <section className="enrol-card" aria-label="Payment methods">
        <div className="enrol-card-head"><div><p className="eyebrow">COLLECTION</p><h2>Payment methods</h2></div></div>
        <LoadState loading={methods.loading} error={methods.error} empty={methods.data?.length === 0} emptyText="No payment methods configured." />
        {methods.data && methods.data.length > 0 && (
          <table className="fin-table">
            <thead><tr><th>Name</th><th>Kind</th><th>Ledger account</th><th>Status</th></tr></thead>
            <tbody>{methods.data.map((method) => {
              const account = accounts.data?.find((item) => item.id === method.account);
              return <tr key={method.id}><td>{method.name}</td><td>{method.kind.replaceAll("_", " ")}</td><td>{account ? `${account.code} ${account.name}` : "—"}</td><td>{method.is_active ? "Active" : "Inactive"}</td></tr>;
            })}</tbody>
          </table>
        )}
        {ctx.canApprove && (
          <form className="enrol-grid" onSubmit={addMethod} aria-label="Add payment method">
            <label>Name<input name="method_name" maxLength={80} required /></label>
            <label>Kind<select name="kind" required defaultValue=""><option value="" disabled>Select kind</option>{["cash", "bank_transfer", "cheque", "mobile_money"].map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label>
            <label>Deposit account<select name="account" required defaultValue=""><option value="" disabled>Select account</option>{accounts.data?.filter((account) => account.is_active && account.type === "asset").map((account) => <option key={account.id} value={account.id}>{account.code} {account.name}</option>)}</select></label>
            <button className="setup-submit" type="submit" disabled={busy}>Add payment method</button>
          </form>
        )}
      </section>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
    </div>
  );
}

function AccountingPeriods({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading, reload } = useFinanceData<AccountingPeriod[]>(ctx, "periods/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function addPeriod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "periods/", {
        method: "POST",
        body: { name: formText(values, "period_name"), start_date: formText(values, "start_date"), end_date: formText(values, "end_date") },
      });
      form.reset();
      setFailed(false);
      setMessage("Accounting period created.");
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(period: AccountingPeriod) {
    const status = period.status === "open" ? "closed" : "open";
    if (status === "closed" && !window.confirm(`Close ${period.name}? New entries cannot be posted into this period.`)) return;
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `periods/${period.id}/`, { method: "PATCH", body: { status } });
      setFailed(false);
      setMessage(`${period.name} ${status}.`);
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <section className="enrol-card" aria-label="Accounting periods">
      <div className="enrol-card-head"><div><p className="eyebrow">PERIOD CONTROL</p><h2>Accounting periods</h2></div></div>
      <p className="fin-note">Closing a period blocks new ledger postings dated inside it. Reopen only when an authorized correction is needed.</p>
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No accounting periods created." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Name</th><th>Starts</th><th>Ends</th><th>Status</th>{ctx.canApprove && <th />}</tr></thead>
          <tbody>{data.map((period) => (
            <tr key={period.id}><td>{period.name}</td><td>{period.start_date}</td><td>{period.end_date}</td><td><StatusPill status={period.status} /></td>
              {ctx.canApprove && <td><button className="link-button" type="button" onClick={() => void setStatus(period)}>{period.status === "open" ? "Close period" : "Reopen period"}</button></td>}
            </tr>
          ))}</tbody>
        </table>
      )}
      {ctx.canApprove && (
        <form className="enrol-grid" onSubmit={addPeriod} aria-label="Create accounting period">
          <label>Name<input name="period_name" maxLength={80} required placeholder="e.g. October 2026" /></label>
          <label>Starts<input name="start_date" type="date" required /></label>
          <label>Ends<input name="end_date" type="date" required /></label>
          <button className="setup-submit" type="submit" disabled={busy}>Create period</button>
        </form>
      )}
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
    </section>
  );
}

export default function FinanceSettingsPage() {
  return (
    <FinanceShell eyebrow="FINANCE ADMIN" title="Settings" active="/finance/settings" description="Configure document numbering, ledger accounts, payment methods and posting periods.">
      {(ctx: FinanceContext) => (
        <div className="enrol-layout">
          <SettingsForm ctx={ctx} />
          <AccountSetup ctx={ctx} />
          <AccountingPeriods ctx={ctx} />
          <p className="fin-note">Currency is currently fixed to GHS. Payment methods are staff-recorded; no online payment provider is configured.</p>
        </div>
      )}
    </FinanceShell>
  );
}

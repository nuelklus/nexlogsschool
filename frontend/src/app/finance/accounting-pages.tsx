"use client";

import { FormEvent, useState } from "react";

import {
  PaymentMethod,
  ExpenseRecord,
  FinanceCampus,
  GeneralLedger,
  JournalRecord,
  LedgerAccount,
  PayablesReconciliation,
  OpeningCsvPreview,
  OpeningBalanceImport,
  PayableRow,
  Supplier,
  SupplierBill,
  SupplierOpeningBalance,
  SupplierTaxCode,
  SupplierVoucher,
  fetchFinanceDocument,
  financeRequest,
  formatMoney,
  newIdempotencyKey,
} from "./finance-api";
import FinanceShell, { FinanceContext, LoadState, Money, StatusPill, useFinanceData } from "./finance-shell";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const value = (form: FormData, name: string) => String(form.get(name) ?? "").trim();
const promptReason = (prompt: string) => {
  const reason = window.prompt(prompt);
  return reason?.trim() || null;
};

// Money may only leave cash, bank or mobile-money accounts; the API enforces the same rule.
function moneyAccounts(accounts: LedgerAccount[] | null | undefined, methods: PaymentMethod[] | null | undefined) {
  const linked = new Set((methods ?? []).filter((method) => method.is_active).map((method) => method.account));
  return (accounts ?? []).filter(
    (item) =>
      item.type === "asset" &&
      item.is_active &&
      !item.is_header &&
      (["cash", "bank", "mobile_money"].includes(item.system_key) || linked.has(item.id)),
  );
}

function ActionMessage({ message, failed }: { message: string; failed: boolean }) {
  if (!message) return null;
  return <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>;
}

function Accounts({ ctx }: { ctx: FinanceContext }) {
  const { data: accounts, error, loading, reload } = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "accounts/", {
        method: "POST",
        body: {
          code: value(values, "code"),
          name: value(values, "name"),
          type: value(values, "type"),
          normal_balance: value(values, "normal_balance"),
          reporting_class: value(values, "reporting_class"),
          parent: value(values, "parent") || undefined,
          is_header: values.get("is_header") === "on",
        },
      });
      form.reset();
      reload();
      setFailed(false);
      setMessage("Account added.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <section className="enrol-card roster" aria-label="Chart of accounts">
      <div className="enrol-card-head"><div><p className="eyebrow">CHART OF ACCOUNTS</p><h2>Accounts</h2></div></div>
      <LoadState loading={loading} error={error} empty={accounts?.length === 0} emptyText="No accounts yet." />
      {accounts && accounts.length > 0 && (
        <table className="fin-table"><thead><tr><th>Code</th><th>Account</th><th>Type</th><th>Normal balance</th><th>Status</th></tr></thead>
          <tbody>{accounts.map((account) => (
            <tr key={account.id}><td>{account.code}</td><td>{account.parent ? "↳ " : ""}{account.name}{account.system_key && <small>System account: {account.system_key}</small>}</td><td>{account.type}{account.is_header ? " · heading" : ""}</td><td>{account.normal_balance}</td><td>{account.is_active ? "Active" : "Inactive"}</td></tr>
          ))}</tbody>
        </table>
      )}
      {ctx.canApprove && (
        <form className="enrol-grid" onSubmit={(event) => void add(event)} aria-label="Add account">
          <label>Code<input name="code" maxLength={20} required /></label>
          <label>Name<input name="name" maxLength={160} required /></label>
          <label>Type<select name="type" required defaultValue=""><option value="" disabled>Select</option>{["asset", "liability", "equity", "income", "expense"].map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>Normal balance<select name="normal_balance" required defaultValue=""><option value="" disabled>Select</option><option value="debit">Debit</option><option value="credit">Credit</option></select></label>
          <label>Reporting class<input name="reporting_class" maxLength={40} /></label>
          <label>Parent heading<select name="parent" defaultValue=""><option value="">No parent</option>{accounts?.filter((item) => item.is_header).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
          <label><span>Heading account</span><input name="is_header" type="checkbox" /></label>
          <button className="setup-submit" type="submit">Add account</button>
        </form>
      )}
      <ActionMessage message={message} failed={failed} />
    </section>
  );
}

function OpeningBalances({ ctx }: { ctx: FinanceContext }) {
  const accounts = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const imports = useFinanceData<OpeningBalanceImport[]>(ctx, "opening-balances/");
  const [lines, setLines] = useState([{ account: "", debit: "", credit: "", description: "" }, { account: "", debit: "", credit: "", description: "" }]);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<OpeningCsvPreview | null>(null);

  async function previewCsv() {
    if (!csvFile) {
      setFailed(true);
      setMessage("Choose a CSV file to preview.");
      return;
    }
    const form = new FormData();
    form.set("file", csvFile);
    try {
      const preview = await financeRequest<OpeningCsvPreview>(
        ctx.authFetch,
        ctx.tenantId,
        "opening-balances/preview-csv/",
        { method: "POST", body: form },
      );
      setCsvPreview(preview);
      setLines(preview.lines.map((line) => ({
        account: line.account,
        debit: line.debit === "0.00" ? "" : line.debit,
        credit: line.credit === "0.00" ? "" : line.credit,
        description: line.description,
      })));
      setFailed(!preview.balanced);
      setMessage(preview.balanced
        ? `CSV preview balanced: ${preview.total_debit} debit and ${preview.total_credit} credit. Review the lines before saving.`
        : `CSV preview is not balanced: ${preview.total_debit} debit and ${preview.total_credit} credit.`);
    } catch (caught) {
      setCsvPreview(null);
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    try {
      const imported = await financeRequest<OpeningBalanceImport>(ctx.authFetch, ctx.tenantId, "opening-balances/", {
        method: "POST",
        body: {
          name: value(values, "name"),
          as_of_date: value(values, "as_of_date"),
          import_reference: value(values, "import_reference"),
          lines: lines.filter((line) => line.account && (line.debit || line.credit)),
        },
      });
      setFailed(false);
      setMessage(`Opening balance import saved as ${imported.status}; it has not affected the ledger.`);
      setLines([{ account: "", debit: "", credit: "", description: "" }, { account: "", debit: "", credit: "", description: "" }]);
      setCsvFile(null);
      setCsvPreview(null);
      form.reset();
      imports.reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function transition(item: OpeningBalanceImport, action: "approve" | "post" | "reverse") {
    const reason = action === "reverse" ? promptReason("Reason for reversal") : undefined;
    if (action === "reverse" && !reason) return;
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `opening-balances/${item.id}/${action}/`, {
        method: "POST",
        body: action === "reverse" ? { reason } : undefined,
      });
      setFailed(false);
      setMessage(`Opening balance ${action} completed.`);
      imports.reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return (
    <div className="enrol-layout">
      <form className="enrol-card" onSubmit={(event) => void create(event)} aria-label="Opening balances">
        <div className="enrol-card-head"><div><p className="eyebrow">MIGRATION</p><h2>Import opening balances</h2></div></div>
        <p className="fin-note">Enter every account balance and make sure debits equal credits. No balancing amount is added automatically. Imports require owner/admin approval before posting.</p>
        <div className="enrol-grid">
          <label>Import name<input name="name" maxLength={120} required /></label>
          <label>As-of date<input name="as_of_date" type="date" required /></label>
          <label>Import reference<input name="import_reference" maxLength={120} /></label>
        </div>
        <div className="enrol-grid">
          <label>Opening-balance CSV (account_code, debit, credit, optional description)
            <input type="file" accept=".csv,text/csv" onChange={(event) => { setCsvFile(event.target.files?.[0] ?? null); setCsvPreview(null); }} />
          </label>
          <button type="button" className="auth-signout" onClick={() => void previewCsv()}>Preview CSV</button>
        </div>
        {csvPreview && <p className={csvPreview.balanced ? "enrol-success" : "setup-error"} role={csvPreview.balanced ? "status" : "alert"}>{csvPreview.lines.length} account lines · Debits {formatMoney(csvPreview.total_debit)} · Credits {formatMoney(csvPreview.total_credit)}</p>}
        {lines.map((line, index) => (
          <div className="enrol-grid" key={index} aria-label={`Opening line ${index + 1}`}>
            <label>Account<select value={line.account} onChange={(event) => { setCsvPreview(null); setLines(lines.map((item, i) => i === index ? { ...item, account: event.target.value } : item)); }}><option value="">Select account</option>{accounts.data?.filter((item) => item.is_active && !item.is_header).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
            <label>Debit<input inputMode="decimal" value={line.debit} onChange={(event) => { setCsvPreview(null); setLines(lines.map((item, i) => i === index ? { ...item, debit: event.target.value, credit: "" } : item)); }} placeholder="0.00" /></label>
            <label>Credit<input inputMode="decimal" value={line.credit} onChange={(event) => { setCsvPreview(null); setLines(lines.map((item, i) => i === index ? { ...item, credit: event.target.value, debit: "" } : item)); }} placeholder="0.00" /></label>
            <label>Description<input value={line.description} onChange={(event) => { setCsvPreview(null); setLines(lines.map((item, i) => i === index ? { ...item, description: event.target.value } : item)); }} maxLength={240} /></label>
          </div>
        ))}
        <div className="enrol-actions">
          <button type="button" className="auth-signout" onClick={() => { setCsvPreview(null); setLines([...lines, { account: "", debit: "", credit: "", description: "" }]); }}>Add line</button>
          <button className="setup-submit" disabled={busy}>{busy ? "Saving…" : "Save import for review"}</button>
        </div>
        <ActionMessage message={message} failed={failed} />
      </form>
      <section className="enrol-card roster" aria-label="Opening balance imports">
        <div className="enrol-card-head"><div><p className="eyebrow">OPENING IMPORTS</p><h2>Review and post</h2></div></div>
        <LoadState loading={imports.loading} error={imports.error} empty={imports.data?.length === 0} emptyText="No opening balance imports." />
        {imports.data && imports.data.length > 0 && <table className="fin-table"><thead><tr><th>Name</th><th>As of</th><th>Status</th><th>Debit</th><th>Credit</th><th /></tr></thead>
          <tbody>{imports.data.map((item) => <tr key={item.id}><td>{item.name}<small>{item.import_reference}</small></td><td>{item.as_of_date}</td><td><StatusPill status={item.status} /></td><td>{item.total_debit ? formatMoney(item.total_debit) : "—"}</td><td>{item.total_credit ? formatMoney(item.total_credit) : "—"}</td>
            <td>{ctx.canApprove && item.status === "draft" && item.created_by_id !== ctx.userId && <button className="link-button" onClick={() => void transition(item, "approve")}>Approve</button>}{" "}
              {ctx.canApprove && item.status === "approved" && <button className="link-button" onClick={() => void transition(item, "post")}>Post</button>}{" "}
              {ctx.canApprove && item.status === "posted" && <button className="link-button" onClick={() => void transition(item, "reverse")}>Reverse</button>}</td>
          </tr>)}</tbody>
        </table>}
      </section>
    </div>
  );
}

function Journals({ ctx }: { ctx: FinanceContext }) {
  const journals = useFinanceData<JournalRecord[]>(ctx, "reports/journals/");
  const accounts = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const [account, setAccount] = useState("");
  const [range, setRange] = useState({ start: "", end: "" });
  const query = new URLSearchParams({ ...(account ? { account } : {}), ...(range.start ? { start: range.start } : {}), ...(range.end ? { end: range.end } : {}) }).toString();
  const ledger = useFinanceData<GeneralLedger>(ctx, account ? `reports/general-ledger/?${query}` : null);
  const [detail, setDetail] = useState<{ number: string; memo: string; lines: { account: string; description: string; debit: string; credit: string }[] } | null>(null);
  const [message, setMessage] = useState("");

  async function openJournal(item: JournalRecord) {
    try {
      setDetail(await financeRequest(ctx.authFetch, ctx.tenantId, `journals/${item.id}/`));
      setMessage("");
    } catch (caught) {
      setMessage(errorText(caught));
    }
  }

  return (
    <div className="enrol-layout">
      <section className="enrol-card roster" aria-label="Journal register">
        <div className="enrol-card-head"><div><p className="eyebrow">POSTED JOURNALS</p><h2>Journal register</h2></div></div>
        <LoadState loading={journals.loading} error={journals.error} empty={journals.data?.length === 0} emptyText="No journal entries." />
        {journals.data && journals.data.length > 0 && <table className="fin-table"><thead><tr><th>Date</th><th>Journal</th><th>Description</th><th>Source</th><th>Debit</th><th>Credit</th><th /></tr></thead>
          <tbody>{journals.data.map((item) => <tr key={item.id}><td>{item.date}</td><td>{item.number}</td><td>{item.memo}</td><td>{item.source_type}</td><td>{formatMoney(item.debit_total)}</td><td>{formatMoney(item.credit_total)}</td><td><button className="link-button" onClick={() => void openJournal(item)}>Details</button></td></tr>)}</tbody>
        </table>}
        {message && <p className="setup-error" role="alert">{message}</p>}
        {detail && <div role="region" aria-label="Journal details"><h3>{detail.number}: {detail.memo}</h3><table className="fin-table"><thead><tr><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead><tbody>{detail.lines.map((line, index) => <tr key={index}><td>{line.account}</td><td>{line.description}</td><td>{formatMoney(line.debit)}</td><td>{formatMoney(line.credit)}</td></tr>)}</tbody></table><button className="link-button" onClick={() => setDetail(null)}>Close details</button></div>}
      </section>
      <section className="enrol-card roster" aria-label="General ledger">
        <div className="enrol-card-head"><div><p className="eyebrow">ACCOUNT ACTIVITY</p><h2>General ledger</h2></div></div>
        <div className="fin-filter">
          <label>Account<select value={account} onChange={(event) => setAccount(event.target.value)}><option value="">Select account</option>{accounts.data?.filter((item) => !item.is_header).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
          <label>From<input type="date" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></label>
          <label>To<input type="date" value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })} /></label>
        </div>
        <LoadState loading={ledger.loading} error={ledger.error} empty={ledger.data?.entries.length === 0} emptyText="No activity for the selected account and period." />
        {ledger.data && <><p className="fin-note">Opening balance: {formatMoney(ledger.data.opening_balance)} · Closing balance: {formatMoney(ledger.data.closing_balance)} (debit less credit)</p>
          {ledger.data.entries.length > 0 && <table className="fin-table"><thead><tr><th>Date</th><th>Journal</th><th>Description</th><th>Campus</th><th>Debit</th><th>Credit</th><th>Running balance</th></tr></thead>
            <tbody>{ledger.data.entries.map((entry, index) => <tr key={`${entry.journal}-${index}`}><td>{entry.date}</td><td>{entry.journal}</td><td>{entry.description}</td><td>{entry.campus || "—"}</td><td>{formatMoney(entry.debit)}</td><td>{formatMoney(entry.credit)}</td><td>{formatMoney(entry.balance)}</td></tr>)}</tbody>
          </table>}</>}
      </section>
    </div>
  );
}

function AccountingBody({ ctx }: { ctx: FinanceContext }) {
  return <div className="enrol-layout"><Accounts ctx={ctx} /><OpeningBalances ctx={ctx} /><Journals ctx={ctx} /></div>;
}

export function AccountingPage() {
  return <FinanceShell eyebrow="ACCOUNTING" title="Accounting" active="/finance/accounting" description="Chart of accounts, reconciled opening balances, posted journals, and account-level ledger activity.">{(ctx) => <AccountingBody ctx={ctx} />}</FinanceShell>;
}

function Suppliers({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading, reload } = useFinanceData<Supplier[]>(ctx, "suppliers/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [statement, setStatement] = useState<{ supplier: Supplier; entries: { date: string; type: string; reference: string; debit: string; credit: string; balance: string }[]; closing_balance: string } | null>(null);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "suppliers/", { method: "POST", body: {
        code: value(values, "code"), name: value(values, "name"), contact_person: value(values, "contact_person"),
        email: value(values, "email"), phone: value(values, "phone"), address: value(values, "address"),
        tax_id: value(values, "tax_id"), payment_terms_days: value(values, "payment_terms_days"), notes: value(values, "notes"),
      } });
      form.reset();
      reload();
      setFailed(false);
      setMessage("Supplier added.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }
  async function showStatement(supplier: Supplier) {
    try {
      setStatement(await financeRequest(ctx.authFetch, ctx.tenantId, `suppliers/${supplier.id}/statement/`));
      setMessage("");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }
  return <div className="enrol-layout">
    {ctx.canApprove && <form className="enrol-card" onSubmit={(event) => void create(event)} aria-label="Add supplier"><div className="enrol-card-head"><div><p className="eyebrow">SUPPLIER DIRECTORY</p><h2>Add supplier</h2></div></div>
      <div className="enrol-grid"><label>Supplier code<input name="code" maxLength={30} required /></label><label>Name<input name="name" maxLength={160} required /></label><label>Contact person<input name="contact_person" maxLength={120} /></label><label>Email<input name="email" type="email" /></label><label>Phone<input name="phone" maxLength={32} /></label><label>Address<input name="address" maxLength={240} /></label><label>Tax ID (optional)<input name="tax_id" maxLength={40} /></label><label>Payment terms (days)<input name="payment_terms_days" type="number" min="0" max="365" defaultValue="30" /></label><label className="enrol-wide">Notes<textarea name="notes" maxLength={2000} /></label></div>
      <button className="setup-submit">Save supplier</button><ActionMessage message={message} failed={failed} /></form>}
    <section className="enrol-card roster" aria-label="Suppliers"><div className="enrol-card-head"><div><p className="eyebrow">VENDORS</p><h2>Suppliers</h2></div></div><LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No suppliers registered." />
      {data && data.length > 0 && <table className="fin-table"><thead><tr><th>Supplier</th><th>Contact</th><th>Phone</th><th>Terms</th><th>Status</th><th /></tr></thead><tbody>{data.map((supplier) => <tr key={supplier.id}><td>{supplier.name}<small>{supplier.code}{supplier.tax_id ? ` · Tax ID ${supplier.tax_id}` : ""}</small></td><td>{supplier.contact_person || supplier.email || "—"}</td><td>{supplier.phone || "—"}</td><td>{supplier.payment_terms_days} days</td><td>{supplier.is_active ? "Active" : "Inactive"}</td><td><button className="link-button" onClick={() => void showStatement(supplier)}>Statement</button></td></tr>)}</tbody></table>}
      {statement && <section aria-label={`Statement for ${statement.supplier.name}`}><h3>{statement.supplier.name} · Closing balance <Money value={statement.closing_balance} /></h3><table className="fin-table"><thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Debit</th><th>Credit</th><th>Balance</th></tr></thead><tbody>{statement.entries.map((entry, index) => <tr key={`${entry.reference}-${index}`}><td>{entry.date}</td><td>{entry.type}</td><td>{entry.reference}</td><td><Money value={entry.debit} /></td><td><Money value={entry.credit} /></td><td><Money value={entry.balance} /></td></tr>)}</tbody></table></section>}</section>
  </div>;
}

export function SuppliersPage() {
  return <FinanceShell eyebrow="ACCOUNTS PAYABLE" title="Suppliers" active="/finance/suppliers" description="Manage tenant-scoped supplier details and review their statements.">{(ctx) => <Suppliers ctx={ctx} />}</FinanceShell>;
}

function Bills({ ctx }: { ctx: FinanceContext }) {
  const suppliers = useFinanceData<Supplier[]>(ctx, "suppliers/");
  const accounts = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const methods = useFinanceData<PaymentMethod[]>(ctx, "payment-methods/");
  const campuses = useFinanceData<FinanceCampus[]>(ctx, "campuses/");
  const taxCodes = useFinanceData<SupplierTaxCode[]>(ctx, "supplier-tax-codes/");
  const { data: bills, error, loading, reload } = useFinanceData<SupplierBill[]>(ctx, "supplier-bills/");
  const vouchers = useFinanceData<SupplierVoucher[]>(ctx, "supplier-vouchers/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [voucherSupplier, setVoucherSupplier] = useState("");
  const [billLines, setBillLines] = useState([
    { account: "", taxCode: "", campus: "", description: "", quantity: "1", unitCost: "" },
  ]);

  async function createTaxCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "supplier-tax-codes/", {
        method: "POST",
        body: {
          code: value(values, "tax_code"),
          name: value(values, "tax_name"),
          rate: value(values, "tax_rate"),
          account: value(values, "tax_account"),
          is_inclusive: values.get("tax_inclusive") === "on",
        },
      });
      form.reset();
      taxCodes.reload();
      setFailed(false);
      setMessage("Purchase-tax code added. The school configured the rate and posting account; no statutory rate was assumed.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function createBill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "supplier-bills/", { method: "POST", body: {
        supplier: value(values, "supplier"), supplier_reference: value(values, "supplier_reference"),
        invoice_date: value(values, "invoice_date"), due_date: value(values, "due_date"),
        lines: billLines.map((line) => ({
          account: line.account,
          tax_code: line.taxCode || undefined,
          campus: line.campus || undefined,
          description: line.description,
          quantity: line.quantity,
          unit_cost: line.unitCost,
        })),
      } });
      form.reset();
      setBillLines([{ account: "", taxCode: "", campus: "", description: "", quantity: "1", unitCost: "" }]);
      reload(); setFailed(false); setMessage("Supplier bill saved as a draft.");
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }

  async function createVoucher(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const bill = value(values, "bill");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "supplier-vouchers/", { method: "POST", idempotencyKey: newIdempotencyKey(), body: {
        supplier: value(values, "voucher_supplier"), payment_date: value(values, "payment_date"),
        account: value(values, "payment_account"), amount: value(values, "voucher_amount"), reference: value(values, "payment_reference"),
        allocations: bill ? [{ bill, amount: value(values, "allocated_amount") || value(values, "voucher_amount") }] : [],
      } });
      form.reset(); vouchers.reload(); setFailed(false); setMessage("Payment voucher prepared; it is not paid until approved and marked paid.");
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }

  async function act(path: string, body?: unknown, reloadData = true) {
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, path, { method: "POST", body });
      setFailed(false); setMessage("Action completed.");
      if (reloadData) reload();
      vouchers.reload();
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }

  async function actWithReason(path: string, prompt: string, reloadData = true) {
    const reason = promptReason(prompt);
    if (!reason) return;
    await act(path, { reason }, reloadData);
  }

  async function uploadDocument(event: FormEvent<HTMLFormElement>, bill: SupplierBill) {
    event.preventDefault();
    const form = event.currentTarget;
    const uploaded = new FormData(form).get("bill_document");
    if (!(uploaded instanceof File)) return;
    const payload = new FormData();
    payload.set("file", uploaded);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `supplier-bills/${bill.id}/documents/`, {
        method: "POST",
        body: payload,
      });
      form.reset();
      setFailed(false);
      setMessage("Supporting document uploaded.");
      reload();
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  async function downloadDocument(documentId: string, filename: string) {
    try {
      const blob = await fetchFinanceDocument(ctx.authFetch, ctx.tenantId, documentId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  const activeBills = bills?.filter((bill) => ["posted", "partially_paid"].includes(bill.status) && (!voucherSupplier || bill.supplier === voucherSupplier)) ?? [];
  return <div className="enrol-layout">
    {ctx.canApprove && <form className="enrol-card" onSubmit={(event) => void createTaxCode(event)} aria-label="Configure purchase tax"><div className="enrol-card-head"><div><p className="eyebrow">PURCHASE TAX</p><h2>Tax codes</h2></div></div>
      <p className="fin-note">Configure rates and accounts according to the school&apos;s applicable tax treatment. Exclusive tax is added to the entered price; inclusive tax is extracted from it. No statutory rates are preconfigured.</p>
      <div className="enrol-grid"><label>Code<input name="tax_code" maxLength={30} required /></label><label>Name<input name="tax_name" maxLength={100} required /></label><label>Rate (%)<input name="tax_rate" inputMode="decimal" min="0" max="100" step="0.01" required /></label><label>Posting account<select name="tax_account" required defaultValue=""><option value="">Select asset or expense</option>{accounts.data?.filter((item) => item.is_active && !item.is_header && ["asset", "expense"].includes(item.type)).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label><label><input name="tax_inclusive" type="checkbox" /> Price includes tax</label></div><button className="setup-submit">Add tax code</button>
      {taxCodes.data && taxCodes.data.length > 0 && <ul>{taxCodes.data.map((item) => <li key={item.id}>{item.code} · {item.name} · {item.rate}% · {item.is_inclusive ? "inclusive" : "exclusive"} · {item.account_name}{!item.is_active ? " · inactive" : ""}</li>)}</ul>}<ActionMessage message={message} failed={failed} />
    </form>}
    <form className="enrol-card" onSubmit={(event) => void createBill(event)} aria-label="Create supplier bill"><div className="enrol-card-head"><div><p className="eyebrow">SUPPLIER INVOICE</p><h2>Record bill</h2></div></div>
      <div className="enrol-grid"><label>Supplier<select name="supplier" required defaultValue=""><option value="">Select supplier</option>{suppliers.data?.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Supplier invoice reference<input name="supplier_reference" required maxLength={80} /></label><label>Invoice date<input name="invoice_date" type="date" required /></label><label>Due date<input name="due_date" type="date" required /></label></div>
      {billLines.map((line, index) => <div className="enrol-grid" key={index} aria-label={`Supplier bill line ${index + 1}`}>
        <label>Expense/asset account<select value={line.account} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, account: event.target.value } : item))} required><option value="">Select account</option>{accounts.data?.filter((item) => item.is_active && !item.is_header && ["asset", "expense"].includes(item.type)).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
        <label>Purchase-tax treatment<select value={line.taxCode} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, taxCode: event.target.value } : item))}><option value="">No tax code</option>{taxCodes.data?.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.rate}% · {item.is_inclusive ? "inclusive" : "exclusive"}</option>)}</select></label>
        <label>Campus / cost centre<select value={line.campus} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, campus: event.target.value } : item))}><option value="">No campus allocation</option>{campuses.data?.filter((item) => item.status === "active").map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
        <label>Description<input value={line.description} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, description: event.target.value } : item))} required maxLength={240} /></label>
        <label>Quantity<input value={line.quantity} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, quantity: event.target.value } : item))} inputMode="decimal" required /></label>
        <label>Unit cost (GHS)<input value={line.unitCost} onChange={(event) => setBillLines(billLines.map((item, i) => i === index ? { ...item, unitCost: event.target.value } : item))} inputMode="decimal" required /></label>
        {billLines.length > 1 && <button className="link-button" type="button" onClick={() => setBillLines(billLines.filter((_, i) => i !== index))}>Remove line</button>}
      </div>)}
      <button className="link-button" type="button" onClick={() => setBillLines([...billLines, { account: "", taxCode: "", campus: "", description: "", quantity: "1", unitCost: "" }])}>Add bill line</button>
      <p className="fin-note">Choose only a tax code approved for your school’s treatment. Bill totals and journals use the configured inclusive/exclusive rate and posting account. Supporting files accept PDF, PNG, or JPEG up to 10 MB.</p><button className="setup-submit">Save bill draft</button><ActionMessage message={message} failed={failed} />
    </form>
    <section className="enrol-card roster" aria-label="Supplier bills"><div className="enrol-card-head"><div><p className="eyebrow">BILLS</p><h2>Approval and posting</h2></div></div><LoadState loading={loading} error={error} empty={bills?.length === 0} emptyText="No supplier bills." />
      {bills && bills.length > 0 && <table className="fin-table">
        <thead><tr><th>Supplier / reference</th><th>Dates</th><th>Status</th><th>Total</th><th>Balance</th><th>Supporting documents</th><th>Action</th></tr></thead>
        <tbody>{bills.map((bill) => <tr key={bill.id}>
          <td>{bill.supplier_name}<small>{bill.supplier_reference}</small></td>
          <td>{bill.invoice_date}<small>Due {bill.due_date}</small></td>
          <td><StatusPill status={bill.status} /></td>
          <td><Money value={bill.total} /></td>
          <td><Money value={bill.balance} /></td>
          <td>
            {bill.documents?.map((document) => <div key={document.id}><button className="link-button" onClick={() => void downloadDocument(document.id, document.name)}>{document.name}</button></div>)}
            {!["cancelled", "reversed"].includes(bill.status) && <form onSubmit={(event) => void uploadDocument(event, bill)}>
              <input aria-label={`Supporting file for ${bill.supplier_reference}`} type="file" name="bill_document" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" required />
              <button className="link-button">Upload</button>
            </form>}
          </td>
          <td>
            {bill.status === "draft" && <button className="link-button" onClick={() => void act(`supplier-bills/${bill.id}/submit/`)}>Submit</button>}
            {bill.status === "submitted" && ctx.canApprove && bill.created_by_id !== ctx.userId && <button className="link-button" onClick={() => void act(`supplier-bills/${bill.id}/approve/`)}>Approve</button>}
            {bill.status === "approved" && <button className="link-button" onClick={() => void act(`supplier-bills/${bill.id}/post/`)}>Post bill</button>}
            {ctx.canApprove && !["cancelled", "reversed"].includes(bill.status) && <button className="link-button" onClick={() => void actWithReason(`supplier-bills/${bill.id}/cancel/`, "Reason for cancellation or reversal")}>Cancel / reverse</button>}
          </td>
        </tr>)}</tbody>
      </table>}
    </section>
    <form className="enrol-card" onSubmit={(event) => void createVoucher(event)} aria-label="Prepare supplier payment"><div className="enrol-card-head"><div><p className="eyebrow">PAYMENT VOUCHER</p><h2>Prepare supplier payment</h2></div></div>
      <div className="enrol-grid"><label>Supplier<select name="voucher_supplier" required value={voucherSupplier} onChange={(event) => setVoucherSupplier(event.target.value)}><option value="">Select supplier</option>{suppliers.data?.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Pay bill (optional)<select name="bill" defaultValue=""><option value="">Record as supplier advance</option>{activeBills.map((bill) => <option key={bill.id} value={bill.id}>{bill.supplier_name} · {bill.supplier_reference} · {bill.balance}</option>)}</select></label><label>Payment date<input name="payment_date" type="date" required /></label><label>Cash/bank/mobile account<select name="payment_account" required defaultValue=""><option value="">Select asset account</option>{moneyAccounts(accounts.data, methods.data).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label><label>Amount<input name="voucher_amount" inputMode="decimal" required /></label><label>Amount allocated to bill<input name="allocated_amount" inputMode="decimal" /></label><label>Payment reference<input name="payment_reference" maxLength={80} /></label></div><button className="setup-submit">Prepare voucher</button>
    </form>
    <section className="enrol-card roster" aria-label="Supplier payment vouchers"><div className="enrol-card-head"><div><p className="eyebrow">VOUCHERS</p><h2>Payment control</h2></div></div><LoadState loading={vouchers.loading} error={vouchers.error} empty={vouchers.data?.length === 0} emptyText="No supplier payment vouchers." />
      {vouchers.data && vouchers.data.length > 0 && <table className="fin-table">
        <thead><tr><th>Voucher</th><th>Supplier</th><th>Date</th><th>Status</th><th>Amount</th><th>Action</th></tr></thead>
        <tbody>{vouchers.data.map((item) => <tr key={item.id}>
          <td>{item.number || "Draft"}<small>{item.reference}</small></td><td>{item.supplier_name}</td><td>{item.payment_date}</td>
          <td><StatusPill status={item.status} /></td><td><Money value={item.amount} /></td>
          <td>
            {item.status === "draft" && ctx.canApprove && item.created_by_id !== ctx.userId && <button className="link-button" onClick={() => void act(`supplier-vouchers/${item.id}/approve/`, undefined, false)}>Approve</button>}
            {item.status === "approved" && <button className="link-button" onClick={() => void act(`supplier-vouchers/${item.id}/pay/`, undefined, false)}>Mark paid</button>}
            {item.status === "paid" && ctx.canApprove && <button className="link-button" onClick={() => void actWithReason(`supplier-vouchers/${item.id}/reverse/`, "Reason for reversal", false)}>Reverse</button>}
          </td>
        </tr>)}</tbody>
      </table>}
    </section>
    <ActionMessage message={message} failed={failed} />
  </div>;
}

export function SupplierBillsPage() {
  return <FinanceShell eyebrow="ACCOUNTS PAYABLE" title="Supplier bills" active="/finance/bills" description="Capture supplier invoices, approve and post payables, then prepare and control payment vouchers.">{(ctx) => <Bills ctx={ctx} />}</FinanceShell>;
}

function Expenses({ ctx }: { ctx: FinanceContext }) {
  const accounts = useFinanceData<LedgerAccount[]>(ctx, "accounts/");
  const methods = useFinanceData<PaymentMethod[]>(ctx, "payment-methods/");
  const campuses = useFinanceData<FinanceCampus[]>(ctx, "campuses/");
  const { data, error, loading, reload } = useFinanceData<ExpenseRecord[]>(ctx, "expenses/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "expenses/", { method: "POST", body: {
        payee: value(fields, "payee"), description: value(fields, "description"),
        expense_account: value(fields, "expense_account"),
        campus: value(fields, "campus") || undefined,
        transaction_date: value(fields, "transaction_date"), amount: value(fields, "amount"),
      } });
      form.reset(); reload(); setFailed(false); setMessage("Expense saved as a draft.");
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }
  async function act(path: string, body?: unknown) {
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, path, { method: "POST", body });
      reload(); setFailed(false); setMessage("Expense workflow updated.");
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }
  async function actWithReason(path: string, prompt: string) {
    const reason = promptReason(prompt);
    if (!reason) return;
    await act(path, { reason });
  }
  async function pay(event: FormEvent<HTMLFormElement>, expense: ExpenseRecord) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `expenses/${expense.id}/pay/`, {
        method: "POST",
        idempotencyKey: newIdempotencyKey(),
        body: {
          amount: value(fields, "payment_amount"),
          account: value(fields, "payment_account"),
          payment_date: value(fields, "payment_date"),
          reference: value(fields, "payment_reference"),
        },
      });
      reload(); form.reset(); setFailed(false); setMessage("Expense payment posted.");
    } catch (caught) { setFailed(true); setMessage(errorText(caught)); }
  }
  return <div className="enrol-layout">
    <form className="enrol-card" onSubmit={(event) => void create(event)} aria-label="Add expense"><div className="enrol-card-head"><div><p className="eyebrow">DIRECT EXPENSE</p><h2>Record expense request</h2></div></div>
      <p className="fin-note">Direct expenses use an approval → payable → payment workflow. Supplier invoices belong in Supplier bills; do not enter the same transaction in both workflows.</p>
      <div className="enrol-grid"><label>Payee<input name="payee" maxLength={160} required /></label><label>Description<input name="description" maxLength={240} required /></label><label>Expense category/account<select name="expense_account" required defaultValue=""><option value="">Select account</option>{accounts.data?.filter((item) => item.type === "expense" && item.is_active && !item.is_header).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label><label>Campus / cost centre<select name="campus" defaultValue=""><option value="">No campus allocation</option>{campuses.data?.filter((item) => item.status === "active").map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label><label>Date<input name="transaction_date" type="date" required /></label><label>Amount (GHS)<input name="amount" inputMode="decimal" required /></label></div><button className="setup-submit">Save expense draft</button>
    </form>
    <section className="enrol-card roster" aria-label="Expense approval queue"><div className="enrol-card-head"><div><p className="eyebrow">APPROVAL QUEUE</p><h2>Expenses</h2></div></div><LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No expenses recorded." />
      {data && data.length > 0 && <table className="fin-table">
        <thead><tr><th>Date / payee</th><th>Category</th><th>Status</th><th>Amount</th><th>Balance</th><th>Action</th></tr></thead>
        <tbody>{data.map((expense) => <tr key={expense.id}>
          <td>{expense.transaction_date}<small>{expense.payee} · {expense.description}</small></td>
          <td>{expense.expense_account_name}</td><td><StatusPill status={expense.status} /></td>
          <td><Money value={expense.amount} /></td><td><Money value={expense.balance} /></td>
          <td>
            {expense.status === "draft" && <button className="link-button" onClick={() => void act(`expenses/${expense.id}/submit/`)}>Submit</button>}
            {expense.status === "submitted" && ctx.canApprove && expense.created_by_id !== ctx.userId && <>
              <button className="link-button" onClick={() => void act(`expenses/${expense.id}/approve/`)}>Approve</button>{" "}
              <button className="link-button" onClick={() => void actWithReason(`expenses/${expense.id}/reject/`, "Reason for rejection")}>Reject</button>
            </>}
            {["approved", "partially_paid"].includes(expense.status) && <form className="fin-inline" onSubmit={(event) => void pay(event, expense)} aria-label={`Pay ${expense.payee}`}>
              <label>Amount<input name="payment_amount" inputMode="decimal" defaultValue={expense.balance} required /></label>
              <label>From<select name="payment_account" required defaultValue=""><option value="">Cash/bank account</option>{moneyAccounts(accounts.data, methods.data).map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select></label>
              <label>Date<input name="payment_date" type="date" defaultValue={new Date().toISOString().slice(0, 10)} required /></label>
              <label>Reference<input name="payment_reference" maxLength={80} /></label><button className="link-button">Pay</button>
            </form>}
            {["approved", "partially_paid", "paid"].includes(expense.status) && ctx.canApprove && expense.amount_paid === "0.00" && <button className="link-button" onClick={() => void actWithReason(`expenses/${expense.id}/reverse/`, "Reason for reversal")}>Reverse</button>}
          </td>
        </tr>)}</tbody>
      </table>}
    </section>
    {message && <ActionMessage message={message} failed={failed} />}
  </div>;
}

export function ExpensesPage() {
  return <FinanceShell eyebrow="EXPENSES" title="Expenses" active="/finance/expenses" description="Submit direct expenses for approval, post them to payables, and record payments separately.">{(ctx) => <Expenses ctx={ctx} />}</FinanceShell>;
}

function Payables({ ctx }: { ctx: FinanceContext }) {
  const { data, error, loading } = useFinanceData<PayableRow[]>(ctx, "reports/accounts-payable/");
  const reconciliation = useFinanceData<PayablesReconciliation>(ctx, "reports/accounts-payable/reconciliation/");
  const suppliers = useFinanceData<Supplier[]>(ctx, "suppliers/");
  const imports = useFinanceData<OpeningBalanceImport[]>(ctx, "opening-balances/");
  const openingBalances = useFinanceData<SupplierOpeningBalance[]>(ctx, "supplier-opening-balances/");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function allocateSupplierOpeningBalance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "supplier-opening-balances/", {
        method: "POST",
        body: {
          opening_import: value(fields, "opening_import"),
          supplier: value(fields, "supplier"),
          debit: value(fields, "debit") || "0.00",
          credit: value(fields, "credit") || "0.00",
        },
      });
      form.reset();
      openingBalances.reload();
      reconciliation.reload();
      setFailed(false);
      setMessage("Supplier opening balance allocated to the existing posted control-account import. No additional journal was created.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorText(caught));
    }
  }

  return <section className="enrol-card roster" aria-label="Accounts payable summary"><div className="enrol-card-head"><div><p className="eyebrow">LIABILITIES</p><h2>Accounts payable ageing</h2></div></div>
    <p className="fin-note">Supplier bill, approved direct-expense, and allocated opening balances. Opening supplier amounts are subledger allocations only; their debits and credits must match the posted accounts-payable control account import.</p>
    {reconciliation.data && <div className={reconciliation.data.reconciled ? "enrol-success" : "setup-error"} role={reconciliation.data.reconciled ? "status" : "alert"}><strong>{reconciliation.data.reconciled ? "Reconciled" : "Out of balance"}</strong> as of {reconciliation.data.as_of} · {reconciliation.data.control_account}: control <Money value={reconciliation.data.control_balance} />, subledger <Money value={reconciliation.data.subledger_balance} />, difference <Money value={reconciliation.data.difference} /></div>}
    {reconciliation.error && <p className="setup-error" role="alert">{reconciliation.error}</p>}
    {ctx.canApprove && <form className="enrol-grid" onSubmit={(event) => void allocateSupplierOpeningBalance(event)} aria-label="Allocate supplier opening balance"><label>Posted opening import<select name="opening_import" required defaultValue=""><option value="">Select import</option>{imports.data?.filter((item) => item.status === "posted").map((item) => <option key={item.id} value={item.id}>{item.name} · {item.as_of_date}</option>)}</select></label><label>Supplier<select name="supplier" required defaultValue=""><option value="">Select supplier</option>{suppliers.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Debit (supplier advance)<input name="debit" inputMode="decimal" placeholder="0.00" /></label><label>Credit (amount payable)<input name="credit" inputMode="decimal" placeholder="0.00" /></label><button className="setup-submit">Allocate opening balance</button><ActionMessage message={message} failed={failed} /></form>}
    {openingBalances.data && openingBalances.data.length > 0 && <table className="fin-table"><thead><tr><th>Supplier</th><th>Opening import</th><th>As of</th><th>Debit</th><th>Credit</th></tr></thead><tbody>{openingBalances.data.map((item) => <tr key={item.id}><td>{item.supplier_name}</td><td>{item.import_name}</td><td>{item.as_of_date}</td><td><Money value={item.debit} /></td><td><Money value={item.credit} /></td></tr>)}</tbody></table>}
    <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No outstanding supplier or expense payables." />
    {data && data.length > 0 && <table className="fin-table"><thead><tr><th>Supplier / payee</th><th>Type</th><th>Open items</th><th>Oldest due</th><th>Current</th><th>1–30 days</th><th>31–60</th><th>61–90</th><th>Over 90</th><th>Total</th></tr></thead><tbody>{data.map((row, index) => <tr key={`${row.supplier ?? row.supplier_name}-${index}`}><td>{row.supplier_name}</td><td>{row.kind === "expense" ? "Direct expense" : "Supplier bills"}</td><td>{row.bills}</td><td>{row.oldest_due_date}</td><td><Money value={row.current} /></td><td><Money value={row.days_1_30} /></td><td><Money value={row.days_31_60} /></td><td><Money value={row.days_61_90} /></td><td><Money value={row.days_over_90} /></td><td><Money value={row.balance} /></td></tr>)}</tbody></table>}
  </section>;
}

export function PayablesPage() {
  return <FinanceShell eyebrow="REPORTING" title="Payables" active="/finance/payables" description="Review outstanding supplier bills and approved expense liabilities by ageing bucket.">{(ctx) => <Payables ctx={ctx} />}</FinanceShell>;
}

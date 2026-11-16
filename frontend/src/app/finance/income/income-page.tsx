"use client";

import { FormEvent, useState } from "react";

import { PaymentMethod, financeRequest, formatMoney, newIdempotencyKey } from "../finance-api";
import FinanceShell, { FinanceContext, LoadState, StatusPill, useFinanceData } from "../finance-shell";
import type { IncomeCategory, OtherIncome } from "./income-api";

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");
const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function RecordForm({ ctx, categories, onDone }: { ctx: FinanceContext; categories: IncomeCategory[]; onDone: () => void }) {
  const methods = useFinanceData<PaymentMethod[]>(ctx, "payment-methods/");
  const [kind, setKind] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await financeRequest<OtherIncome>(ctx.authFetch, ctx.tenantId, "other-income/", {
        method: "POST",
        idempotencyKey: key,
        body: {
          category: text(values, "category"),
          method: text(values, "method"),
          amount: text(values, "amount"),
          income_date: text(values, "income_date") || undefined,
          payer_name: text(values, "payer_name"),
          reference: text(values, "reference"),
          description: text(values, "description"),
        },
      });
      setFailed(false);
      setMessage("Income recorded.");
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
    <form className="enrol-card" onSubmit={submit} aria-label="Record other income">
      <div className="enrol-card-head"><div><p className="eyebrow">NEW INCOME</p><h2>Record other income</h2></div></div>
      <p className="fin-note">For money the school receives that is not a student fee, such as donations, canteen sales or rentals.</p>
      <div className="enrol-grid">
        <label>Source
          <select name="category" required defaultValue="">
            <option value="" disabled>Select source</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label>Received by
          <select name="method" required defaultValue="" onChange={(event) => setKind(methods.data?.find((m) => m.id === event.target.value)?.kind ?? "")}>
            <option value="" disabled>Select method</option>
            {methods.data?.filter((m) => m.is_active).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        <label>Amount (GHS)<input name="amount" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required placeholder="0.00" /></label>
        <label>Date<input name="income_date" type="date" /></label>
        <label>{kind === "mobile_money" ? "Transaction ID" : kind === "cheque" ? "Cheque number" : "Reference"}
          <input name="reference" maxLength={80} required={kind !== "" && kind !== "cash"} />
        </label>
        <label>Received from (optional)<input name="payer_name" maxLength={120} /></label>
        <label className="enrol-wide">Description (optional)<input name="description" maxLength={240} /></label>
      </div>
      {message && <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>{message}</p>}
      <div className="enrol-actions"><button className="setup-submit" type="submit" disabled={busy}>{busy ? "Saving…" : "Record income"}</button></div>
    </form>
  );
}

function AddCategory({ ctx, onDone }: { ctx: FinanceContext; onDone: () => void }) {
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = text(new FormData(form), "name");
    if (!name) return;
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, "income-categories/", { method: "POST", body: { name } });
      form.reset();
      setMessage("");
      onDone();
    } catch (caught) {
      setMessage(errorText(caught));
    }
  }

  return (
    <form className="fin-inline" onSubmit={submit} aria-label="Add income source">
      <label>New income source<input name="name" maxLength={80} required placeholder="e.g. Sponsorship" /></label>
      <button className="setup-submit" type="submit">Add source</button>
      {message && <p className="setup-error" role="alert">{message}</p>}
    </form>
  );
}

function History({ ctx, categories, refresh }: { ctx: FinanceContext; categories: IncomeCategory[]; refresh: number }) {
  const [category, setCategory] = useState("");
  const { data, error, loading, reload } = useFinanceData<OtherIncome[]>(ctx, `other-income/?${new URLSearchParams({ ...(category ? { category } : {}), r: String(refresh) })}`);
  const [message, setMessage] = useState("");

  async function reverse(record: OtherIncome) {
    const reason = window.prompt("Reason for reversing this income?");
    if (!reason?.trim()) return;
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `other-income/${record.id}/reverse/`, { method: "POST", body: { reason: reason.trim() } });
      reload();
    } catch (caught) {
      setMessage(errorText(caught));
    }
  }

  return (
    <section className="enrol-card roster" aria-label="Other income history">
      <div className="enrol-card-head">
        <div><p className="eyebrow">HISTORY</p><h2>Other income</h2></div>
        <select aria-label="Filter by source" value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">All sources</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      {message && <p className="setup-error" role="alert">{message}</p>}
      <LoadState loading={loading} error={error} empty={data?.length === 0} emptyText="No other income recorded yet." />
      {data && data.length > 0 && (
        <table className="fin-table">
          <thead><tr><th>Date</th><th>Source</th><th>Method</th><th className="num">Amount</th><th>Status</th><th /></tr></thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id}>
                <td>{r.income_date}</td>
                <td>{r.category_name}<small>{[r.payer_name, r.reference].filter(Boolean).join(" · ")}</small></td>
                <td>{r.method_name}</td>
                <td className="num">{formatMoney(r.amount)}</td>
                <td><StatusPill status={r.status} /></td>
                <td>{ctx.canApprove && r.status === "confirmed" && <button className="link-button" onClick={() => void reverse(r)}>Reverse</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function OtherIncomeSection({ ctx }: { ctx: FinanceContext }) {
  const [refresh, setRefresh] = useState(0);
  const categories = useFinanceData<IncomeCategory[]>(ctx, `income-categories/?r=${refresh}`);
  const list = categories.data ?? [];
  return (
    <div className="fin-stack">
      <RecordForm ctx={ctx} categories={list} onDone={() => setRefresh((n) => n + 1)} />
      {ctx.canApprove && <AddCategory ctx={ctx} onDone={() => setRefresh((n) => n + 1)} />}
      <History ctx={ctx} categories={list} refresh={refresh} />
    </div>
  );
}

export default function OtherIncomePage() {
  return (
    <FinanceShell
      eyebrow="INCOME"
      title="Other income"
      active="/finance/income"
      description="Record money the school receives that is not a student fee, by source and by how it was received."
    >
      {(ctx) => <OtherIncomeSection ctx={ctx} />}
    </FinanceShell>
  );
}

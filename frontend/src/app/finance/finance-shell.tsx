"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useState } from "react";

import AuthControls from "../auth-controls";
import { AuthMembership, useAuth } from "../auth-provider";
import { AuthFetch, SubscriptionExpiredError } from "../school/student-api";
import { ExpiredNotice } from "../school/subscription-panel";
import WorkspaceSidebar from "../workspace-sidebar";
import { APPROVER_ROLES, FINANCE_ROLES, financeRequest, formatMoney } from "./finance-api";

export type FinanceContext = {
  tenantId: string;
  userId: string;
  authFetch: AuthFetch;
  canApprove: boolean;
  onFailure: (error: unknown) => void;
};

const TABS = [
  { href: "/finance", label: "Dashboard" },
  { href: "/finance/fees", label: "Fees" },
  { href: "/finance/billing", label: "Billing" },
  { href: "/finance/invoices", label: "Invoices" },
  { href: "/finance/services", label: "Services" },
  { href: "/finance/payments", label: "Payments" },
  { href: "/finance/arrears", label: "Arrears" },
  { href: "/finance/advances", label: "Advances" },
  { href: "/finance/transactions", label: "Transactions" },
  { href: "/finance/reports", label: "Reports" },
  { href: "/finance/reminders", label: "Reminders" },
  { href: "/finance/settings", label: "Settings" },
  { href: "/finance/accounting", label: "Accounting" },
  { href: "/finance/suppliers", label: "Suppliers" },
  { href: "/finance/bills", label: "Supplier bills" },
  { href: "/finance/expenses", label: "Expenses" },
  { href: "/finance/payables", label: "Payables" },
];

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.");

// Loads a finance endpoint. A 402 is routed to the shell so the whole page shows the renewal notice.
export function useFinanceData<T>(ctx: FinanceContext, path: string | null) {
  const [state, setState] = useState<{ data: T | null; error: string }>({ data: null, error: "" });
  const [tick, setTick] = useState(0);
  const { authFetch, tenantId, onFailure } = ctx;

  useEffect(() => {
    if (path === null) return;
    let current = true;
    financeRequest<T>(authFetch, tenantId, path)
      .then((data) => current && setState({ data, error: "" }))
      .catch((error: unknown) => {
        if (!current) return;
        if (error instanceof SubscriptionExpiredError) onFailure(error);
        else setState({ data: null, error: errorText(error) });
      });
    return () => {
      current = false;
    };
  }, [authFetch, tenantId, path, tick, onFailure]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data: state.data, error: state.error, loading: path !== null && state.data === null && !state.error, reload };
}

function Body({
  membership,
  memberships,
  onSelect,
  eyebrow,
  title,
  description,
  actions,
  children,
  active,
}: {
  membership: AuthMembership;
  memberships: AuthMembership[];
  onSelect: (tenantId: string) => void;
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
  children: (ctx: FinanceContext) => ReactNode;
  active: string;
}) {
  const { authFetch, user } = useAuth();
  const [expired, setExpired] = useState("");
  const onFailure = useCallback((error: unknown) => {
    if (error instanceof SubscriptionExpiredError) setExpired(error.message);
  }, []);
  const canApprove = APPROVER_ROLES.includes(membership.role);
  const ctx: FinanceContext = { tenantId: membership.tenant_id, userId: user?.id ?? "", authFetch, canApprove, onFailure };

  return (
    <main className="workspace">
      <WorkspaceSidebar memberships={memberships} membership={membership} onSelect={onSelect} active="finance" />
      <section className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Finance <span>/</span> {title}</div>
          <div className="topbar-actions"><AuthControls /></div>
        </header>
        <div className="content">
          <section className="page-heading">
            <div>
              <p className="eyebrow">{eyebrow}</p>
              <h1>{title}</h1>
              <p className="heading-copy">{description}</p>
            </div>
            {actions}
          </section>
          <nav className="finance-tabs" aria-label="Finance sections">
            {TABS.map((tab) => (
              <Link key={tab.href} href={tab.href} className={tab.href === active ? "active" : ""} aria-current={tab.href === active ? "page" : undefined}>
                {tab.label}
              </Link>
            ))}
          </nav>
          {expired ? (
            <ExpiredNotice message={expired} canManage={canApprove} tenantId={membership.tenant_id} authFetch={authFetch} />
          ) : (
            children(ctx)
          )}
        </div>
      </section>
    </main>
  );
}

// Owners, school administrators and accountants only; teachers, parents and students are sent back to the root.
export default function FinanceShell(props: {
  eyebrow: string;
  title: string;
  description: string;
  active: string;
  actions?: ReactNode;
  children: (ctx: FinanceContext) => ReactNode;
}) {
  const { user } = useAuth();
  const router = useRouter();
  const [selected, setSelected] = useState("");
  const allowed = user?.memberships.filter((item) => FINANCE_ROLES.includes(item.role)) ?? [];

  useEffect(() => {
    if (user && allowed.length === 0) router.replace("/");
  }, [user, allowed.length, router]);

  if (!user || allowed.length === 0) return null;
  const membership = allowed.find((item) => item.tenant_id === selected) ?? allowed[0];
  return <Body key={membership.tenant_id} membership={membership} memberships={allowed} onSelect={setSelected} {...props} />;
}

export function Money({ value }: { value: string | null | undefined }) {
  return <span className="money">{formatMoney(value)}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const tone = ["paid", "confirmed", "issued", "active"].includes(status)
    ? "ready"
    : ["voided", "failed", "reversed", "overdue"].includes(status)
      ? "danger"
      : "pending";
  return (
    <span className={`state-pill ${tone}`}>
      <span className="state-dot" />
      {status.replaceAll("_", " ")}
    </span>
  );
}

export function LoadState({ loading, error, empty, emptyText }: { loading: boolean; error: string; empty?: boolean; emptyText?: string }) {
  if (error) return <p className="setup-error" role="alert">{error}</p>;
  if (loading) return <p className="roster-empty" role="status">Loading…</p>;
  if (empty) return <p className="roster-empty">{emptyText ?? "Nothing to show yet."}</p>;
  return null;
}

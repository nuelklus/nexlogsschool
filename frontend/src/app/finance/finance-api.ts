import { AuthFetch, requestError } from "../school/student-api";

export const FINANCE_ROLES = ["owner", "school_admin", "accountant"];
export const APPROVER_ROLES = ["owner", "school_admin"];

export type Invoice = {
  id: string;
  number: string;
  status: "draft" | "issued" | "partially_paid" | "paid" | "credited" | "voided";
  overdue: boolean;
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  academic_year: string;
  academic_year_name: string;
  period_label: string;
  issue_date: string | null;
  due_date: string;
  subtotal: string;
  discount_total: string;
  total: string;
  amount_paid: string;
  credited_total: string;
  balance: string;
  calculation?: BillingCalculation | null;
  lines?: { id: string; kind: string; description: string; amount: string; quantity: number | null; unit_price: string | null }[];
  payments?: { payment: string; receipt_number: string; amount: string; date: string }[];
  credit_notes?: { number: string; amount: string; reason: string; reason_type: string }[];
};

export type Payment = {
  id: string;
  student: string;
  student_name: string;
  method: string;
  method_name: string;
  method_kind: string;
  amount: string;
  payment_date: string;
  reference: string;
  payer_name: string;
  status: "pending" | "confirmed" | "failed" | "reversed";
  receipt_number: string;
  allocated_total: string;
  refunded_total: string;
  unallocated: string;
  reversal_reason: string;
};

export type Receipt = {
  receipt_number: string;
  status: string;
  school: string;
  date: string;
  student: { name: string; admission_number: string; grade_level: string };
  payer_name: string;
  method: string;
  reference: string;
  amount: string;
  allocations: { invoice: string; amount: string }[];
  advance_credit: string;
};

export type Summary = {
  total_invoiced: string;
  total_discounts: string;
  total_collected: string;
  pending_unconfirmed: string;
  outstanding_balance: string;
  overdue_balance: string;
  overdue_invoices: number;
  student_advances_held: string;
  collections_by_method: { method: string; amount: string }[];
  collections_by_campus: { campus: string; amount: string }[];
  collections_by_class: { grade_level: string; amount: string }[];
  collections_by_date: { date: string; amount: string }[];
  basis: Record<string, string>;
};

export type Statement = {
  student: { id: string; name: string; admission_number: string; grade_level: string };
  entries: {
    date: string;
    type: string;
    reference: string;
    description: string;
    debit: string;
    credit: string;
    balance: string;
  }[];
  closing_balance: string;
  outstanding_invoices: string;
  advance_credit: string;
};

export type ArrearsRow = {
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  invoices: number;
  balance: string;
  overdue_balance: string;
  oldest_due_date: string;
};

export type AdvanceRow = {
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  advance: string;
  open_balance: string;
  payments: { payment: string; receipt_number: string; advance: string }[];
};

export type Transaction = {
  date: string;
  entry: string;
  memo: string;
  source: string;
  account: string;
  debit: string;
  credit: string;
};

export type FeeCategory = { id: string; code: string; name: string; is_active: boolean };
export type FeeSchedule = {
  id: string;
  academic_year: string;
  category: string;
  category_name: string;
  grade_level: string;
  amount: string;
  effective_from: string;
  effective_to: string | null;
  status: "active" | "inactive";
  applicability: Applicability;
  billing_basis: "fixed" | "per_unit";
  unit_label: string;
  condition_type: "" | "gender" | "new_student" | "service";
  condition_value: string;
};

export type Applicability = "compulsory" | "optional" | "conditional" | "individual";

export const APPLICABILITY_LABELS: Record<Applicability, string> = {
  compulsory: "Compulsory",
  optional: "Optional service",
  conditional: "Conditional",
  individual: "Individual student",
};

export type Enrollment = {
  id: string;
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  category: string;
  category_name: string;
  status: "active" | "suspended" | "cancelled";
  start_date: string;
  end_date: string | null;
  notes: string;
  history?: { id: string; action: string; detail: Record<string, unknown>; created_at: string }[];
};

export type Usage = {
  id: string;
  student: string;
  student_name: string;
  category: string;
  category_name: string;
  period_label: string;
  quantity: number;
};

export type Adjustment = {
  id: string;
  student: string;
  student_name: string;
  kind: "scholarship" | "discount" | "charge" | "exemption";
  category: string | null;
  description: string;
  reason: string;
  amount: string | null;
  percent: string | null;
  status: string;
};

export type BillingCalculation = {
  calculated_on: string;
  fees: { description: string; amount: string; applicability: string; quantity: number | null; unit_price: string | null }[];
  charges: { description: string; amount: string }[];
  discounts: { description: string; amount: string }[];
  excluded: { description: string; applicability: string; code: string; reason: string }[];
};

export type PreviewRow = {
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  calculation: BillingCalculation | null;
  totals: { subtotal: string; discount_total: string; total: string } | null;
  issues: string[];
  can_generate: boolean;
};

export type Preview = { students: PreviewRow[]; ready: number; blocked: number; total: string };
export type PaymentMethod = { id: string; name: string; kind: string; account: string; is_active: boolean };
export type FinanceSettings = {
  currency: string;
  invoice_prefix: string;
  receipt_prefix: string;
  credit_note_prefix: string;
  default_payment_terms_days: number;
};
export type LedgerAccount = {
  id: string;
  code: string;
  name: string;
  type: "asset" | "liability" | "equity" | "income" | "expense";
  system_key: string;
  is_active: boolean;
  parent: string | null;
  normal_balance: "debit" | "credit";
  reporting_class: string;
  is_header: boolean;
};
export type AccountingPeriod = {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: "open" | "closed";
};
export type TrialBalance = {
  rows: { account: string; type: string; debit: string; credit: string }[];
  total_debit: string;
  total_credit: string;
  balanced: boolean;
};
export type ReminderCandidate = {
  student: string;
  student_name: string;
  admission_number: string;
  grade_level: string;
  invoices: number;
  balance: string;
  overdue_balance: string;
  oldest_due_date: string;
  phones: string[];
};
export type Supplier = {
  id: string;
  code: string;
  name: string;
  contact_person: string;
  email: string;
  phone: string;
  address: string;
  tax_id: string;
  payment_terms_days: number;
  notes: string;
  is_active: boolean;
};
export type FinanceCampus = { id: string; code: string; name: string; status: string };
export type SupplierBill = {
  id: string;
  supplier: string;
  supplier_name: string;
  supplier_reference: string;
  invoice_date: string;
  due_date: string;
  status: string;
  total: string;
  amount_paid: string;
  balance: string;
  created_by_id: string | null;
  approved_by_id: string | null;
  lines?: {
    id: string;
    account: string;
    account_name: string;
    tax_code: string | null;
    tax_code_name: string;
    tax_rate: string;
    tax_amount: string;
    description: string;
    quantity: string;
    unit_cost: string;
    amount: string;
    line_total: string;
  }[];
  documents?: { id: string; name: string; content_type: string; file_size: number; created_at: string }[];
};
export type SupplierTaxCode = {
  id: string;
  code: string;
  name: string;
  rate: string;
  account: string;
  account_name: string;
  is_inclusive: boolean;
  is_active: boolean;
};
export type SupplierOpeningBalance = {
  id: string;
  opening_import: string;
  import_name: string;
  as_of_date: string;
  supplier: string;
  supplier_name: string;
  debit: string;
  credit: string;
};
export type PayablesReconciliation = {
  as_of: string;
  control_account: string;
  control_balance: string;
  subledger_balance: string;
  difference: string;
  reconciled: boolean;
};
export type OpeningCsvPreview = {
  lines: { account: string; account_code: string; account_name: string; debit: string; credit: string; description: string }[];
  total_debit: string;
  total_credit: string;
  balanced: boolean;
};
export type SupplierVoucher = {
  id: string;
  number: string;
  supplier: string;
  supplier_name: string;
  payment_date: string;
  account: string;
  account_name: string;
  amount: string;
  reference: string;
  status: string;
  created_by_id: string | null;
  approved_by_id: string | null;
};
export type ExpenseRecord = {
  id: string;
  payee: string;
  description: string;
  expense_account: string;
  expense_account_name: string;
  campus: string | null;
  transaction_date: string;
  amount: string;
  amount_paid: string;
  balance: string;
  status: string;
  rejection_reason: string;
  created_by_id: string | null;
  approved_by_id: string | null;
};
export type OpeningBalanceImport = {
  id: string;
  name: string;
  as_of_date: string;
  import_reference: string;
  status: string;
  created_by_id?: string | null;
  total_debit?: string;
  total_credit?: string;
  balanced?: boolean;
  lines?: { account: string; account_code: string; account_name: string; debit: string; credit: string; description: string }[];
};
export type JournalRecord = {
  id: string;
  number: string;
  date: string;
  memo: string;
  source_type: string;
  source_id: string;
  status: string;
  debit_total: string;
  credit_total: string;
  created_by: string;
};
export type GeneralLedger = {
  account: { id: string; code: string; name: string; type: string };
  opening_balance: string;
  closing_balance: string;
  balance_basis: string;
  entries: { date: string; journal: string; source_type: string; source_id: string; description: string; campus: string; debit: string; credit: string; balance: string }[];
};
export type PayableRow = {
  supplier: string | null;
  supplier_name: string;
  kind?: string;
  bills: number;
  balance: string;
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  days_over_90: string;
  oldest_due_date: string;
};

const formatter = new Intl.NumberFormat("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Amounts arrive as decimal strings; they are only formatted for display, never summed in the browser.
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? `GHS ${formatter.format(number)}` : "—";
}

export const statusLabel = (status: string) => status.replaceAll("_", " ");

export function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function financeRequest<T>(
  authFetch: AuthFetch,
  tenantId: string,
  path: string,
  init: { method?: "GET" | "POST" | "PATCH"; body?: unknown; idempotencyKey?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (typeof FormData !== "undefined" && init.body instanceof FormData) {
    body = init.body;
  } else if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.body);
  }
  if (init.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;
  const response = await authFetch(`/tenants/${tenantId}/finance/${path}`, {
    method: init.method ?? "GET",
    headers,
    body,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload as T;
}

export async function fetchFinanceDocument(authFetch: AuthFetch, tenantId: string, documentId: string): Promise<Blob> {
  const response = await authFetch(`/tenants/${tenantId}/finance/supplier-bill-documents/${documentId}/`);
  if (!response.ok) throw new Error("The supporting document could not be downloaded.");
  return response.blob();
}

export async function fetchPdf(authFetch: AuthFetch, tenantId: string, paymentId: string): Promise<Blob> {
  const response = await authFetch(`/tenants/${tenantId}/finance/payments/${paymentId}/receipt.pdf`);
  if (!response.ok) throw new Error("The receipt PDF could not be loaded.");
  return response.blob();
}

export type IncomeCategory = { id: string; name: string; is_active: boolean };

export type OtherIncome = {
  id: string;
  category: string;
  category_name: string;
  method: string;
  method_name: string;
  method_kind: string;
  amount: string;
  income_date: string;
  payer_name: string;
  reference: string;
  description: string;
  status: "confirmed" | "reversed";
  reversal_reason: string;
};

export type MoneyReceived = {
  currency: string;
  total: string;
  count: number;
  truncated: boolean;
  by_kind: { kind: string; amount: string }[];
  by_method: { method: string; amount: string }[];
  by_source: { source: string; amount: string }[];
  rows: {
    date: string;
    source: string;
    category: string;
    from: string;
    grade_level: string;
    method: string;
    kind: string;
    reference: string;
    amount: string;
    status: string;
  }[];
};

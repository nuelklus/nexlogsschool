import { AuthFetch, getErrorMessage } from "./student-api";

export type SubscriptionPlan = {
  code: string;
  name: string;
  is_trial: boolean;
  // Null for the free trial; otherwise any interval the backend supports.
  billing_interval: string | null;
  price: string;
  currency: string;
};

export type SubscriptionStatus = {
  status: string;
  has_access: boolean;
  plan: SubscriptionPlan | null;
  started_at: string | null;
  current_period_start: string | null;
  expires_at: string | null;
  renewal: { instructions: string; support_email: string };
};

const INTERVAL_LABELS: Record<string, string> = {
  monthly: "Monthly (every month)",
  termly: "Termly (every 3 months)",
  yearly: "Yearly (every 12 months)",
};

const STATUS_LABELS: Record<string, string> = {
  trialing: "Free trial",
  active: "Active",
  expired: "Expired",
  cancelled: "Cancelled",
  suspended: "Suspended",
  none: "No subscription",
};

const words = (value: string) => {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

// Unknown intervals fall back to their raw name rather than being hidden or mislabelled.
export const intervalLabel = (interval: string) => INTERVAL_LABELS[interval] ?? words(interval);

export const statusLabel = (status: string) => STATUS_LABELS[status] ?? words(status);

export function priceLabel(plan: SubscriptionPlan): string {
  return plan.is_trial ? "Free" : `${plan.currency} ${plan.price}`;
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const isNullableString = (value: unknown) => value === null || typeof value === "string";

export function isSubscriptionStatus(value: unknown): value is SubscriptionStatus {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const plan = v.plan as Record<string, unknown> | null;
  const renewal = v.renewal as Record<string, unknown> | null;
  return (
    typeof v.status === "string" &&
    typeof v.has_access === "boolean" &&
    (plan === null ||
      (typeof plan === "object" &&
        typeof plan.code === "string" &&
        typeof plan.name === "string" &&
        typeof plan.is_trial === "boolean" &&
        isNullableString(plan.billing_interval) &&
        typeof plan.price === "string" &&
        typeof plan.currency === "string")) &&
    isNullableString(v.started_at) &&
    isNullableString(v.current_period_start) &&
    isNullableString(v.expires_at) &&
    typeof renewal === "object" &&
    renewal !== null &&
    typeof renewal.instructions === "string" &&
    typeof renewal.support_email === "string"
  );
}

export async function getSubscription(
  authFetch: AuthFetch,
  tenantId: string,
): Promise<SubscriptionStatus> {
  const response = await authFetch(`/tenants/${tenantId}/subscription/`);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(getErrorMessage(payload));
  if (!isSubscriptionStatus(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

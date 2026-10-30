import { describe, expect, it, vi } from "vitest";

import {
  formatDate,
  getSubscription,
  intervalLabel,
  isSubscriptionStatus,
  priceLabel,
  statusLabel,
} from "./subscription-api";

const termly = {
  status: "active",
  has_access: true,
  plan: {
    code: "termly",
    name: "Termly",
    is_trial: false,
    billing_interval: "termly",
    price: "300.00",
    currency: "GHS",
  },
  started_at: "2026-10-26T09:00:00Z",
  current_period_start: "2026-10-26T09:00:00Z",
  expires_at: "2027-01-26T09:00:00Z",
  renewal: { instructions: "", support_email: "" },
};

const respond = (body: unknown, status = 200) =>
  vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status })));

describe("subscription helpers", () => {
  it("labels monthly, termly and yearly intervals", () => {
    expect(intervalLabel("monthly")).toBe("Monthly (every month)");
    expect(intervalLabel("termly")).toBe("Termly (every 3 months)");
    expect(intervalLabel("yearly")).toBe("Yearly (every 12 months)");
  });

  it("falls back to a readable name for an interval it does not know", () => {
    expect(intervalLabel("half_yearly")).toBe("Half yearly");
  });

  it("labels statuses, including ones it does not know", () => {
    expect(statusLabel("trialing")).toBe("Free trial");
    expect(statusLabel("none")).toBe("No subscription");
    expect(statusLabel("past_due")).toBe("Past due");
  });

  it("shows the real price and currency, and Free only for the trial", () => {
    expect(priceLabel(termly.plan)).toBe("GHS 300.00");
    expect(priceLabel({ ...termly.plan, is_trial: true, price: "0.00" })).toBe("Free");
  });

  it("formats dates and tolerates missing or invalid ones", () => {
    expect(formatDate("2027-01-26T09:00:00Z")).toBe("26 Jan 2027");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });

  it("validates the API payload", () => {
    expect(isSubscriptionStatus(termly)).toBe(true);
    expect(isSubscriptionStatus({ ...termly, plan: null })).toBe(true);
    expect(isSubscriptionStatus({ ...termly, plan: { code: "x" } })).toBe(false);
    expect(isSubscriptionStatus({ ...termly, renewal: undefined })).toBe(false);
    expect(isSubscriptionStatus(null)).toBe(false);
  });

  it("fetches the school's own subscription", async () => {
    const authFetch = respond(termly);
    await expect(getSubscription(authFetch, "t1")).resolves.toEqual(termly);
    expect(authFetch).toHaveBeenCalledWith("/tenants/t1/subscription/");
  });

  it("surfaces API errors and malformed responses", async () => {
    await expect(getSubscription(respond({ detail: "Not found." }, 404), "t1")).rejects.toThrow(
      "Not found.",
    );
    await expect(getSubscription(respond({ nope: true }), "t1")).rejects.toThrow(
      "unexpected response",
    );
  });
});

"use client";

import { useEffect, useState } from "react";

import { AuthFetch } from "./student-api";
import {
  SubscriptionStatus,
  formatDate,
  getSubscription,
  intervalLabel,
  priceLabel,
  statusLabel,
} from "./subscription-api";

const DEFAULT_RENEWAL_TEXT =
  "Contact NLSchool to renew your subscription. Renewals are recorded by the NLSchool team.";

function RenewalHelp({ renewal }: { renewal: SubscriptionStatus["renewal"] }) {
  return (
    <div className="subscription-renewal">
      <h3>How to renew</h3>
      <p>{renewal.instructions || DEFAULT_RENEWAL_TEXT}</p>
      {renewal.support_email && (
        <p>
          Support: <a href={`mailto:${renewal.support_email}`}>{renewal.support_email}</a>
        </p>
      )}
    </div>
  );
}

function Details({ subscription }: { subscription: SubscriptionStatus }) {
  const { plan } = subscription;
  const endLabel = subscription.status === "trialing" ? "Trial ends" : "Expires";
  return (
    <dl className="subscription-details">
      <div><dt>Status</dt><dd>{statusLabel(subscription.status)}</dd></div>
      {plan && (
        <>
          <div><dt>Plan</dt><dd>{plan.name}</dd></div>
          {plan.billing_interval && (
            <div><dt>Billing</dt><dd>{intervalLabel(plan.billing_interval)}</dd></div>
          )}
          <div><dt>Price</dt><dd>{priceLabel(plan)}</dd></div>
          <div><dt>Started</dt><dd>{formatDate(subscription.started_at)}</dd></div>
          <div><dt>{endLabel}</dt><dd>{formatDate(subscription.expires_at)}</dd></div>
        </>
      )}
    </dl>
  );
}

export function SubscriptionPanel({
  tenantId,
  authFetch,
}: {
  tenantId: string;
  authFetch: AuthFetch;
}) {
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    getSubscription(authFetch, tenantId)
      .then((result) => current && setSubscription(result))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [authFetch, tenantId]);

  return (
    <section className="subscription-panel" aria-label="Subscription">
      <h2>Subscription</h2>
      {failed ? (
        <p className="school-note">Subscription details are unavailable right now.</p>
      ) : subscription === null ? (
        <p role="status">Loading subscription…</p>
      ) : (
        <>
          <Details subscription={subscription} />
          <RenewalHelp renewal={subscription.renewal} />
        </>
      )}
    </section>
  );
}

// Shown in place of school data when the API answers 402. Nothing here unlocks access.
export function ExpiredNotice({
  message,
  canManage,
  tenantId,
  authFetch,
}: {
  message: string;
  canManage: boolean;
  tenantId: string;
  authFetch: AuthFetch;
}) {
  return (
    <>
      <div className="subscription-expired" role="alert">
        <h2>This school&apos;s subscription has expired</h2>
        <p>{message}</p>
        <p>
          {canManage
            ? "Student records are unavailable until the subscription is renewed. Your subscription details and renewal steps are below."
            : "Please ask your school administrator to renew the subscription. You can sign in again once it has been renewed."}
        </p>
      </div>
      {canManage && <SubscriptionPanel tenantId={tenantId} authFetch={authFetch} />}
    </>
  );
}

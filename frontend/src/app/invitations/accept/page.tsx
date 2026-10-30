import { Suspense } from "react";

import StaffInvitationAcceptanceForm from "./staff-invitation-acceptance-form";

type InvitationPageProps = {
  searchParams: Promise<{ token?: string | string[] }>;
};

export default function StaffInvitationAcceptancePage({
  searchParams,
}: InvitationPageProps) {
  return (
    <Suspense fallback={<main className="setup-page"><div className="setup-shell">Loading invitation…</div></main>}>
      <InvitationTokenPage searchParams={searchParams} />
    </Suspense>
  );
}

async function InvitationTokenPage({ searchParams }: InvitationPageProps) {
  const { token } = await searchParams;

  return (
    <StaffInvitationAcceptanceForm
      token={typeof token === "string" ? token : ""}
    />
  );
}

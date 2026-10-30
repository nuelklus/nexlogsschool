import { Suspense } from "react";

import AuthActionForm from "../../auth-actions";

type PasswordResetPageProps = {
  searchParams: Promise<{ token?: string | string[] }>;
};

export default function PasswordResetConfirmPage(props: PasswordResetPageProps) {
  return (
    <Suspense fallback={<main className="setup-page"><div className="setup-shell">Loading password reset…</div></main>}>
      <ResetTokenPage {...props} />
    </Suspense>
  );
}

async function ResetTokenPage({ searchParams }: PasswordResetPageProps) {
  const { token } = await searchParams;
  return <AuthActionForm mode="password-reset-confirm" token={typeof token === "string" ? token : ""} />;
}

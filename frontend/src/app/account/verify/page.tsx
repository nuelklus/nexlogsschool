import { Suspense } from "react";

import AuthActionForm from "../../auth-actions";

type VerificationPageProps = {
  searchParams: Promise<{ token?: string | string[] }>;
};

export default function VerifyEmailPage(props: VerificationPageProps) {
  return (
    <Suspense fallback={<main className="setup-page"><div className="setup-shell">Loading verification link…</div></main>}>
      <VerificationTokenPage {...props} />
    </Suspense>
  );
}

async function VerificationTokenPage({ searchParams }: VerificationPageProps) {
  const { token } = await searchParams;
  return <AuthActionForm mode="verify" token={typeof token === "string" ? token : ""} />;
}

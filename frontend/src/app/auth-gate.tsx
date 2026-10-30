"use client";

import { useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";

import { useAuth } from "./auth-provider";

export default function AuthGate({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) router.replace("/login");
  }, [isLoading, user, router]);

  if (isLoading || !user) {
    return (
      <main className="setup-page">
        <div className="setup-shell">
          <div className="setup-state" role="status">
            {isLoading ? "Checking your session…" : "Redirecting to sign in…"}
          </div>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}

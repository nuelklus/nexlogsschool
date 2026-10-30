"use client";

import Link from "next/link";

import { useAuth } from "./auth-provider";

export default function AuthControls() {
  const { user, isLoading, error, signOut } = useAuth();

  if (isLoading) return <span className="auth-control-muted">Checking session…</span>;
  if (!user) {
    return (
      <div className="auth-controls">
        {error && <span className="auth-control-error" role="status">{error}</span>}
        <Link className="admin-link" href="/login">Sign in <span aria-hidden="true">→</span></Link>
      </div>
    );
  }

  return (
    <div className="auth-controls">
      <Link className="admin-link" href="/school">Students</Link>
      <span className="auth-control-muted">{user.full_name}</span>
      <button className="auth-signout" type="button" onClick={() => void signOut()}>
        Sign out
      </button>
      {error && <span className="auth-control-error" role="alert">{error}</span>}
    </div>
  );
}

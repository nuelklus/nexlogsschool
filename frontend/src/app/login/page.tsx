"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { useAuth } from "../auth-provider";

export default function LoginPage() {
  const router = useRouter();
  const { user, isLoading, error: sessionError, signIn, signOut } = useAuth();
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setMessage("");
    try {
      await signIn(String(form.get("email") ?? "").trim(), String(form.get("password") ?? ""));
      router.push("/");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "We couldn’t sign you in. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="setup-page">
      <div className="setup-shell">
        <header className="setup-header">
          <Link className="setup-brand" href="/">
            <span className="setup-brand-mark">N</span>
            <span>NLSchool</span>
          </Link>
          <Link className="setup-back" href="/">Back to overview</Link>
        </header>
        <section className="setup-card">
          <div className="setup-step"><span>ACCOUNT ACCESS</span><span>SECURE SIGN IN</span></div>
          {isLoading ? (
            <div className="setup-state" role="status">Checking your session…</div>
          ) : user ? (
            <div className="setup-result" role="status">
              <span className="setup-result-icon" aria-hidden="true">✓</span>
              <p className="setup-eyebrow">SIGNED IN</p>
              <h1>Welcome, {user.full_name}.</h1>
              <p>{user.email}</p>
              {user.memberships.length ? (
                <ul className="auth-memberships">
                  {user.memberships.map((membership) => (
                    <li key={membership.tenant_id}>
                      <strong>{membership.tenant_name}</strong>
                      <span>{membership.role.replaceAll("_", " ")}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>This account has no active school membership yet.</p>
              )}
              <div className="auth-result-actions">
                <Link className="setup-submit" href="/">Open my dashboard <span aria-hidden="true">→</span></Link>
                <button className="auth-text-button" type="button" onClick={() => void signOut()}>Sign out</button>
              </div>
            </div>
          ) : (
            <>
              <div className="setup-intro">
                <p className="setup-eyebrow">WELCOME BACK</p>
                <h1>Sign in to NLSchool.</h1>
                <p>Use the same email and password for your school account, whether you are an owner or invited staff member.</p>
              </div>
              <form className="setup-form" onSubmit={handleSubmit}>
                <label>
                  Email address
                  <input name="email" type="email" autoComplete="username" required />
                </label>
                <label>
                  Password
                  <input name="password" type="password" autoComplete="current-password" required />
                </label>
                {(message || sessionError) && <p className="setup-error" role="alert">{message || sessionError}</p>}
                <div className="setup-form-footer auth-form-footer">
                  <span>
                    <Link href="/password-reset">Forgot password?</Link>
                    <Link href="/account/verification-request">Resend verification</Link>
                  </span>
                  <button className="setup-submit" type="submit" disabled={submitting}>
                    {submitting ? "Signing in…" : "Sign in"} <span aria-hidden="true">→</span>
                  </button>
                </div>
              </form>
            </>
          )}
        </section>
        <footer className="setup-footer">NLSchool <span>·</span> School operations</footer>
      </div>
    </main>
  );
}

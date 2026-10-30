"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

type ActionMode =
  | "verify"
  | "verification-request"
  | "password-reset-request"
  | "password-reset-confirm";

const titles: Record<ActionMode, { eyebrow: string; title: string; copy: string }> = {
  verify: {
    eyebrow: "EMAIL VERIFICATION",
    title: "Verify your email address.",
    copy: "Confirm the email address for your NLSchool account to enable sign in.",
  },
  "verification-request": {
    eyebrow: "EMAIL VERIFICATION",
    title: "Get a new verification link.",
    copy: "Enter your account email. If it needs verification, we’ll send a fresh link.",
  },
  "password-reset-request": {
    eyebrow: "ACCOUNT RECOVERY",
    title: "Reset your password.",
    copy: "Enter your account email and we’ll send a password reset link if an active account exists.",
  },
  "password-reset-confirm": {
    eyebrow: "ACCOUNT RECOVERY",
    title: "Choose a new password.",
    copy: "Use a strong password you haven’t used for this account before.",
  },
};

function getErrorMessage(payload: unknown): string {
  if (typeof payload === "object" && payload !== null) {
    const detail = "detail" in payload ? payload.detail : undefined;
    if (typeof detail === "string") return detail;
    for (const [field, value] of Object.entries(payload)) {
      if (typeof value === "string") return `${field.replaceAll("_", " ")}: ${value}`;
      if (Array.isArray(value) && typeof value[0] === "string") {
        return `${field.replaceAll("_", " ")}: ${value[0]}`;
      }
    }
  }
  return "The request could not be completed. Please try again.";
}

export default function AuthActionForm({
  mode,
  token = "",
}: {
  mode: ActionMode;
  token?: string;
}) {
  const [message, setMessage] = useState(
    (mode === "verify" || mode === "password-reset-confirm") && !token
      ? "This link is missing its token. Request a new link and try again."
      : "",
  );
  const [complete, setComplete] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const config = titles[mode];

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    if ((mode === "verify" || mode === "password-reset-confirm") && !token) {
      setMessage("This link is missing its token. Request a new link and try again.");
      return;
    }

    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    if (mode === "password-reset-confirm" && password !== String(form.get("password_confirmation") ?? "")) {
      setMessage("The passwords do not match.");
      return;
    }

    const endpoint = {
      verify: "/auth/email/verify/",
      "verification-request": "/auth/email/verification-request/",
      "password-reset-request": "/auth/password-reset/",
      "password-reset-confirm": "/auth/password-reset/confirm/",
    }[mode];
    const body = mode === "verify"
      ? { token }
      : mode === "password-reset-confirm"
        ? { token, new_password: password }
        : { email };

    setSubmitting(true);
    setMessage("");
    try {
      const response = await fetch(`${API_URL}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(result));
      setComplete(true);
      setMessage(
        typeof result === "object" && result !== null && "detail" in result && typeof result.detail === "string"
          ? result.detail
          : "Request completed.",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The request could not be completed. Try again.");
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
          <Link className="setup-back" href="/login">Back to sign in</Link>
        </header>
        <section className="setup-card">
          <div className="setup-step"><span>{config.eyebrow}</span><span>SECURE ACCOUNT</span></div>
          <div className="setup-intro">
            <p className="setup-eyebrow">{config.eyebrow}</p>
            <h1>{complete ? "You’re all set." : config.title}</h1>
            <p>{complete ? message : config.copy}</p>
          </div>
          {!complete && (
            <form className="setup-form" onSubmit={handleSubmit}>
              {(mode === "verification-request" || mode === "password-reset-request") && (
                <label>
                  Email address
                  <input name="email" type="email" autoComplete="email" required />
                </label>
              )}
              {mode === "password-reset-confirm" && (
                <>
                  <label>
                    New password
                    <input name="password" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
                  </label>
                  <label>
                    Confirm new password
                    <input name="password_confirmation" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
                  </label>
                </>
              )}
              {message && <p className="setup-error" role="alert">{message}</p>}
              <div className="setup-form-footer auth-action-footer">
                <span>Links are single-use and expire for your security.</span>
                <button className="setup-submit" type="submit" disabled={submitting || ((mode === "verify" || mode === "password-reset-confirm") && !token)}>
                  {submitting ? "Please wait…" : mode === "verify" ? "Verify email" : mode === "password-reset-confirm" ? "Update password" : "Send link"}
                  <span aria-hidden="true">→</span>
                </button>
              </div>
            </form>
          )}
          {complete && (
            <div className="auth-result-actions">
              <Link className="setup-submit" href="/login">Continue to sign in <span aria-hidden="true">→</span></Link>
            </div>
          )}
        </section>
        <footer className="setup-footer">NLSchool <span>·</span> School operations</footer>
      </div>
    </main>
  );
}

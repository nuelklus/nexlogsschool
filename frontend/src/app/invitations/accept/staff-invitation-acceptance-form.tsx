"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

type FormState = "ready" | "submitting" | "complete" | "invalid";

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
  return "We couldn’t accept this invitation. Check your details and try again.";
}

export default function StaffInvitationAcceptanceForm({ token }: { token: string }) {
  const [formState, setFormState] = useState<FormState>(token ? "ready" : "invalid");
  const [message, setMessage] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (formState === "submitting" || !token) return;

    const formData = new FormData(event.currentTarget);
    const fullName = String(formData.get("full_name") ?? "").trim();
    const password = String(formData.get("password") ?? "");
    const passwordConfirmation = String(formData.get("password_confirmation") ?? "");

    if (password !== passwordConfirmation) {
      setMessage("The passwords do not match.");
      return;
    }

    setFormState("submitting");
    setMessage("");
    try {
      const response = await fetch(`${API_URL}/invitations/accept/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, full_name: fullName, password }),
      });
      const result: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(result));
      setFormState("complete");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "We couldn’t accept this invitation. Try again.");
      setFormState("ready");
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
          <div className="setup-step"><span>SCHOOL INVITATION</span><span>01 / 01</span></div>
          {formState === "complete" ? (
            <div className="setup-result" role="status">
              <span className="setup-result-icon" aria-hidden="true">✓</span>
              <p className="setup-eyebrow">INVITATION ACCEPTED</p>
              <h1>Your school membership is active.</h1>
              <p>You can now return to NLSchool. Your invitation link can no longer be reused.</p>
              <Link className="setup-submit" href="/">Return to overview <span aria-hidden="true">→</span></Link>
            </div>
          ) : formState === "invalid" ? (
            <div className="setup-state" role="alert">
              <p className="setup-eyebrow">INVITATION LINK</p>
              <h1>This invitation link is missing its token.</h1>
              <p>Open the complete link from the invitation email, or ask the school administrator to send a new invitation.</p>
              <Link className="setup-submit" href="/">Return to overview <span aria-hidden="true">→</span></Link>
            </div>
          ) : (
            <>
              <div className="setup-intro">
                <p className="setup-eyebrow">JOIN YOUR SCHOOL</p>
                <h1>Accept your invitation.</h1>
                <p>Confirm your details to activate your school membership. If you already have an NLSchool account, enter its current password.</p>
              </div>
              <form className="setup-form" onSubmit={handleSubmit}>
                <label>
                  Full name
                  <input name="full_name" autoComplete="name" maxLength={160} required />
                </label>
                <label>
                  Password
                  <input name="password" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
                </label>
                <label>
                  Confirm password
                  <input name="password_confirmation" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
                </label>
                {message && <p className="setup-error" role="alert">{message}</p>}
                <div className="setup-form-footer">
                  <span>Your invitation expires after 7 days and can only be used once.</span>
                  <button className="setup-submit" type="submit" disabled={formState === "submitting"}>
                    {formState === "submitting" ? "Accepting invitation…" : "Accept invitation"} <span aria-hidden="true">→</span>
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

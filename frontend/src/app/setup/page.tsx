"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

type SetupState = "checking" | "available" | "unavailable" | "error" | "complete";

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
  return "We couldn’t complete setup. Check the details and try again.";
}

export default function SetupPage() {
  const [setupState, setSetupState] = useState<SetupState>("checking");
  const [message, setMessage] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let isCurrent = true;

    fetch(`${API_URL}/setup/`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not check setup availability.");
        return response.json() as Promise<{ available: boolean; reason: string | null }>;
      })
      .then(({ available, reason }) => {
        if (!isCurrent) return;
        setSetupState(available ? "available" : "unavailable");
        if (reason === "disabled") {
          setMessage("Configure NLSCHOOL_SETUP_TOKEN on this deployment to enable initial setup.");
        }
      })
      .catch(() => {
        if (isCurrent) {
          setMessage("The setup service is unavailable. Make sure the NLSchool API is running.");
          setSetupState("error");
        }
      });

    return () => {
      isCurrent = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    const formData = new FormData(event.currentTarget);
    const setupToken = String(formData.get("setup_token") ?? "").trim();
    const payload = {
      school_name: String(formData.get("school_name") ?? "").trim(),
      school_email: String(formData.get("school_email") ?? "").trim(),
      school_phone: String(formData.get("school_phone") ?? "").trim(),
      country: String(formData.get("country") ?? "").trim(),
      region: String(formData.get("region") ?? "").trim(),
      address: String(formData.get("address") ?? "").trim(),
      school_type: String(formData.get("school_type") ?? "").trim(),
      owner_name: String(formData.get("owner_name") ?? "").trim(),
      owner_email: String(formData.get("owner_email") ?? "").trim(),
      owner_password: String(formData.get("owner_password") ?? ""),
    };

    setMessage("");
    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_URL}/setup/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(setupToken ? { "X-NLSchool-Setup-Token": setupToken } : {}),
        },
        body: JSON.stringify(payload),
      });
      const result: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(result));

      const owner = typeof result === "object" && result !== null && "owner" in result
        ? result.owner
        : undefined;
      if (typeof owner === "object" && owner !== null && "email" in owner && typeof owner.email === "string") {
        setOwnerEmail(owner.email);
      }
      setSetupState("complete");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "We couldn’t complete setup. Try again.");
    } finally {
      setIsSubmitting(false);
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
          <div className="setup-step"><span>FIRST SCHOOL</span><span>01 / 01</span></div>
          {setupState === "complete" ? (
            <div className="setup-result" role="status">
              <span className="setup-result-icon" aria-hidden="true">✓</span>
              <p className="setup-eyebrow">YOU’RE READY</p>
              <h1>Your school is connected.</h1>
              <p>
                The school and owner account have been created. The owner account is
                registered to <strong>{ownerEmail}</strong>. Check that inbox and
                verify the address before signing in.
              </p>
              <Link className="setup-submit" href="/login">Continue to sign in <span aria-hidden="true">→</span></Link>
            </div>
          ) : setupState === "checking" ? (
            <div className="setup-state" role="status">Checking setup availability…</div>
          ) : setupState === "unavailable" ? (
            <div className="setup-state" role="status">
              <p className="setup-eyebrow">{message ? "SETUP DISABLED" : "SETUP COMPLETE"}</p>
              <h1>{message ? "Initial setup is not enabled." : "A school is already connected."}</h1>
              <p>{message || "Initial setup can only be completed once."}</p>
              {!message && <Link className="setup-submit" href="/register">Register a school <span aria-hidden="true">→</span></Link>}
              <Link className="setup-back" href="/">Return to overview</Link>
            </div>
          ) : setupState === "error" ? (
            <div className="setup-state" role="alert">
              <p className="setup-eyebrow">CONNECTION ISSUE</p>
              <h1>We can’t reach the setup service.</h1>
              <p>{message}</p>
            </div>
          ) : (
            <>
              <div className="setup-intro">
                <p className="setup-eyebrow">A GOOD PLACE TO START</p>
                <h1>Let’s set up your school.</h1>
                <p>Add the school and owner details. You can configure campuses, academic years, and staff access next.</p>
              </div>

              <form className="setup-form" onSubmit={handleSubmit}>
                <fieldset>
                  <legend><span>01</span> School details</legend>
                  <label>
                    School name <span className="required-mark">*</span>
                    <input name="school_name" autoComplete="organization" maxLength={200} required />
                  </label>
                  <div className="setup-field-grid">
                    <label>
                      School email <span className="required-mark">*</span>
                      <input name="school_email" type="email" autoComplete="email" required />
                    </label>
                    <label>
                      Phone number <span className="required-mark">*</span>
                      <input name="school_phone" type="tel" autoComplete="tel" maxLength={32} required />
                    </label>
                  </div>
                  <div className="setup-field-grid">
                    <label>
                      Country
                      <input name="country" autoComplete="country-name" defaultValue="Ghana" maxLength={100} required />
                    </label>
                    <label>
                      Region
                      <input name="region" autoComplete="address-level1" maxLength={100} />
                    </label>
                  </div>
                  <div className="setup-field-grid">
                    <label>
                      Address
                      <input name="address" autoComplete="street-address" maxLength={240} />
                    </label>
                    <label>
                      School type
                      <input name="school_type" placeholder="e.g. Private, public" maxLength={80} />
                    </label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend><span>02</span> Owner account</legend>
                  <label>
                    Full name <span className="required-mark">*</span>
                    <input name="owner_name" autoComplete="name" maxLength={160} required />
                  </label>
                  <label>
                    Owner email <span className="required-mark">*</span>
                    <input name="owner_email" type="email" autoComplete="username" required />
                  </label>
                  <label>
                    Password <span className="required-mark">*</span>
                    <input name="owner_password" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
                    <span className="field-hint">Use at least 8 characters and avoid common passwords.</span>
                  </label>
                </fieldset>

                <details className="setup-advanced">
                  <summary>Production setup access</summary>
                  <label>
                    One-time setup token
                    <input name="setup_token" type="password" autoComplete="off" />
                    <span className="field-hint">Required only when your deployment administrator has configured NLSCHOOL_SETUP_TOKEN.</span>
                  </label>
                </details>

                {message && <p className="setup-error" role="alert">{message}</p>}
                <div className="setup-form-footer">
                  <span>Your owner account will be assigned full school access.</span>
                  <button className="setup-submit" type="submit" disabled={isSubmitting}>
                    {isSubmitting ? "Creating school…" : "Create school"} <span aria-hidden="true">→</span>
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

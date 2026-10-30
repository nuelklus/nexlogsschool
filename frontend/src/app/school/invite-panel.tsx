"use client";

import { FormEvent, useState } from "react";

import { STAFF_ROLES, inviteStaff } from "./invitation-api";
import { AuthFetch } from "./student-api";
import { formatDate } from "./subscription-api";

export function StaffInvitePanel({
  tenantId,
  authFetch,
}: {
  tenantId: string;
  authFetch: AuthFetch;
}) {
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      const invitation = await inviteStaff(authFetch, tenantId, {
        email: String(data.get("email") ?? "").trim(),
        role: String(data.get("role") ?? ""),
      });
      setMessage(
        `Invitation sent to ${invitation.email}. It expires on ${formatDate(invitation.expires_at)}.`,
      );
      setFailed(false);
      form.reset();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Something went wrong. Try again.");
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="subscription-panel" aria-label="Invite staff">
      <h2>Invite staff</h2>
      <p className="school-note">
        They receive an email with a link to create their account and join your school.
      </p>
      <form className="school-invite" onSubmit={submit} aria-label="Invite staff member">
        <input name="email" type="email" placeholder="Email address" aria-label="Staff email" required />
        <select name="role" aria-label="Staff role" defaultValue="teacher">
          {STAFF_ROLES.map((role) => (
            <option key={role.value} value={role.value}>{role.label}</option>
          ))}
        </select>
        <button className="auth-signout" type="submit" disabled={busy}>
          {busy ? "Sending…" : "Send invitation"}
        </button>
        {message && (
          <p className={failed ? "setup-error" : "school-note"} role={failed ? "alert" : "status"}>
            {message}
          </p>
        )}
      </form>
    </section>
  );
}

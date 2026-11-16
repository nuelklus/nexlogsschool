"use client";

import { FormEvent, useState } from "react";

import { financeRequest } from "./finance-api";
import { FinanceContext, LoadState, useFinanceData } from "./finance-shell";

type Campus = { id: string; name: string; code: string; status: string };
type Accountant = { membership: string; name: string; email: string; status: string; campuses: string[] };

function GrantForm({ ctx, member, campuses, onSaved }: {
  ctx: FinanceContext; member: Accountant; campuses: Campus[]; onSaved: () => void;
}) {
  const [selected, setSelected] = useState(member.campuses);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await financeRequest(ctx.authFetch, ctx.tenantId, `campus-access/${member.membership}/`, {
        method: "PUT", body: { campuses: selected },
      });
      setMessage("Campus access saved.");
      onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Campus access could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form" onSubmit={save}>
      <h3>{member.name || member.email} · {member.status}</h3>
      {campuses.map((campus) => (
        <label key={campus.id} className="academic-checkbox">
          <input type="checkbox" checked={selected.includes(campus.id)} onChange={(event) => {
            setSelected((current) => event.target.checked
              ? [...current, campus.id] : current.filter((id) => id !== campus.id));
          }} />
          {campus.name} ({campus.code}){campus.status !== "active" ? " · inactive" : ""}
        </label>
      ))}
      {error && <p className="setup-error" role="alert">{error}</p>}
      {message && <p className="enrol-success" role="status">{message}</p>}
      <button className="setup-submit" type="submit" disabled={busy}>Save campus access for {member.name || member.email}</button>
    </form>
  );
}

export default function CampusAccessPanel({ ctx }: { ctx: FinanceContext }) {
  const members = useFinanceData<Accountant[]>(ctx, "campus-access/");
  const campuses = useFinanceData<Campus[]>(ctx, "campuses/");
  const campusList = Array.isArray(campuses.data) ? campuses.data : null;
  const memberList = Array.isArray(members.data) ? members.data : null;
  const malformed = (members.data !== null && !memberList) || (campuses.data !== null && !campusList);
  return (
    <section className="enrol-card" aria-label="Finance campus access">
      <h2>Accountant campus access</h2>
      <p className="fin-note">Accountants can view and issue invoices and prepare expenses only for explicitly granted campuses. Unassigned records and school-wide finance features remain restricted to owners and administrators.</p>
      <LoadState loading={members.loading || campuses.loading} error={members.error || campuses.error}
        empty={memberList?.length === 0} emptyText="No accountants are registered." />
      {malformed && <p className="setup-error" role="alert">The server returned an unexpected campus access response.</p>}
      {campusList?.length === 0 && <p className="fin-note">Create a campus before granting access.</p>}
      {memberList && campusList && memberList.map((member) => (
        <GrantForm key={`${member.membership}:${member.campuses.slice().sort().join(",")}`}
          ctx={ctx} member={member} campuses={campusList} onSaved={members.reload} />
      ))}
    </section>
  );
}

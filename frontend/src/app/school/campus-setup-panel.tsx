"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { requestError, type AuthFetch } from "./student-api";

type Campus = {
  id: string; name: string; code: string; address: string;
  contact_phone: string; contact_email: string; status: "active" | "inactive";
};

function isCampus(value: unknown): value is Campus {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return ["id", "name", "code", "address", "contact_phone", "contact_email"].every((key) => typeof item[key] === "string")
    && (item.status === "active" || item.status === "inactive");
}

export default function CampusSetupPanel({ tenantId, authFetch, onChanged }: {
  tenantId: string; authFetch: AuthFetch; onChanged: () => void;
}) {
  const [campuses, setCampuses] = useState<Campus[] | null>(null);
  const [editing, setEditing] = useState<Campus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  const path = `/tenants/${tenantId}/campuses/`;
  const load = useCallback(async () => {
    const response = await authFetch(path);
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) throw requestError(response, data);
    if (!Array.isArray(data) || !data.every(isCampus)) throw new Error("Unexpected campus list response.");
    return data;
  }, [authFetch, path]);
  useEffect(() => {
    let active = true;
    load().then((items) => { if (active) { setCampuses(items); setError(""); } })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "Could not load campuses."); });
    return () => { active = false; };
  }, [load, attempt]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = Object.fromEntries(["name", "code", "address", "contact_phone", "contact_email", "status"]
      .map((key) => [key, String(values.get(key) ?? "").trim()]));
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await authFetch(editing ? `${path}${editing.id}/` : path, {
        method: editing ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data: unknown = await response.json().catch(() => null);
      if (!response.ok) throw requestError(response, data);
      if (!isCampus(data)) throw new Error("Unexpected save response. Reload campuses before retrying.");
      const wasEditing = Boolean(editing);
      setEditing(null); form.reset();
      onChanged();
      try {
        setCampuses(await load());
        setMessage(wasEditing ? "Campus updated." : "Campus created.");
      } catch {
        setCampuses(null);
        setError("The campus was saved, but the list could not be refreshed. Reload campuses before making another change.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save campus.");
    } finally { setBusy(false); }
  }

  return <section className="academic-card" id="campuses" aria-label="School campuses">
    <p className="eyebrow">SCHOOL SETUP</p>
    <h2>Campuses</h2>
    <p>Create at least one campus before creating classes. A class campus cannot be changed after enrollment. Deactivating a campus preserves existing records.</p>
    {error && <p className="setup-error" role="alert">{error}</p>}
    {message && <p className="academic-success" role="status">{message}</p>}
    {!campuses && !error && <p role="status">Loading campuses…</p>}
    {!campuses && error && <button type="button" onClick={() => setAttempt((value) => value + 1)}>Reload campuses</button>}
    {campuses && <>
      {campuses.length === 0 ? <p>No campuses yet. Create your first campus.</p> : <ul className="academic-records">
        {campuses.map((campus) => <li key={campus.id}>
          <strong>{campus.name} · {campus.code}</strong><span>{campus.status}{campus.address ? ` · ${campus.address}` : ""}</span>
          <button type="button" disabled={busy} onClick={() => { setEditing(campus); setError(""); setMessage(""); }}>Edit {campus.name}</button>
        </li>)}
      </ul>}
      <form key={editing?.id ?? "new"} className="academic-form" onSubmit={(event) => void save(event)}>
        <h3>{editing ? `Edit ${editing.name}` : "Create campus"}</h3>
        <label>Campus name <input name="name" required maxLength={160} defaultValue={editing?.name ?? ""} /></label>
        <label>Campus code <input name="code" required maxLength={40} defaultValue={editing?.code ?? ""} /></label>
        <label>Address <input name="address" maxLength={240} defaultValue={editing?.address ?? ""} /></label>
        <label>Contact phone <input name="contact_phone" maxLength={32} defaultValue={editing?.contact_phone ?? ""} /></label>
        <label>Contact email <input name="contact_email" type="email" defaultValue={editing?.contact_email ?? ""} /></label>
        <label>Status <select name="status" defaultValue={editing?.status ?? "active"}><option value="active">Active</option><option value="inactive">Inactive</option></select></label>
        <button type="submit" disabled={busy}>{editing ? "Save campus changes" : "Create campus"}</button>
        {editing && <button type="button" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>}
      </form>
    </>}
  </section>;
}

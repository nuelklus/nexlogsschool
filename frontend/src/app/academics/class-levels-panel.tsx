"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AuthFetch, requestError } from "../school/student-api";
import { listClassLevels, type SchoolClassLevel } from "./academics-api";

type CatalogueLevel = {
  code: string;
  name: string;
  sort_order: number;
};

async function request(authFetch: AuthFetch, url: string, init?: RequestInit): Promise<unknown> {
  const response = await authFetch(url, init);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCatalogueLevel(value: unknown): value is CatalogueLevel {
  return isRecord(value) && typeof value.code === "string" && typeof value.name === "string"
    && typeof value.sort_order === "number";
}

export default function ClassLevelsPanel({ authFetch, tenantId, canManage }: {
  authFetch: AuthFetch;
  tenantId: string;
  canManage: boolean;
}) {
  const [catalogue, setCatalogue] = useState<CatalogueLevel[]>([]);
  const [levels, setLevels] = useState<SchoolClassLevel[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const base = `/tenants/${tenantId}/academics/`;

  const load = useCallback(async () => {
    const [available, configured] = await Promise.all([
      request(authFetch, `${base}class-level-catalogue/`),
      listClassLevels(authFetch, tenantId),
    ]);
    if (!Array.isArray(available) || !available.every(isCatalogueLevel)) {
      throw new Error("The server returned an unexpected class-level response.");
    }
    return { available, configured };
  }, [authFetch, base, tenantId]);

  useEffect(() => {
    let active = true;
    load().then(({ available, configured }) => {
      if (!active) return;
      setCatalogue(available);
      setLevels(configured);
      setHasLoaded(true);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "Class levels could not be loaded.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [load]);

  async function retry() {
    setLoading(true);
    setError("");
    try {
      const { available, configured } = await load();
      setCatalogue(available);
      setLevels(configured);
      setHasLoaded(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Class levels could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  async function save(url: string, method: "POST" | "PATCH", body: object) {
    setBusy(true);
    setError("");
    setMessage("");
    let saved = false;
    try {
      await request(authFetch, url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      saved = true;
      const { available, configured } = await load();
      setCatalogue(available);
      setLevels(configured);
      setMessage("Class level saved.");
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "The request could not be completed.";
      setError(saved ? `The level was saved, but refreshing failed: ${detail}` : detail);
    } finally {
      setBusy(false);
    }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const code = String(values.get("catalogue_level") ?? "");
    const chosen = catalogue.find((level) => level.code === code);
    const displayName = String(values.get("display_name") ?? "").trim();
    if (!chosen && !displayName) {
      setError("Enter a name for the custom level.");
      return;
    }
    void save(`${base}class-levels/`, "POST", {
      catalogue_level: code || null,
      display_name: displayName || chosen?.name,
      sort_order: Number(values.get("sort_order") || chosen?.sort_order || 0),
    });
  }

  function edit(event: FormEvent<HTMLFormElement>, level: SchoolClassLevel) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    void save(`${base}class-levels/${level.id}/`, "PATCH", {
      display_name: String(values.get("display_name") ?? "").trim(),
      sort_order: Number(values.get("sort_order")),
      is_active: values.get("is_active") === "on",
      is_terminal: values.get("is_terminal") === "on",
    });
  }

  const unused = catalogue.filter((item) => !levels.some((level) => level.catalogue_level === item.code));
  return (
    <section className="academic-card" aria-label="Class levels">
      <h2>School class levels</h2>
      <p className="fin-note">Choose the levels your school offers or add a custom level. This setup does not yet change existing classes or finance.</p>
      <p className="fin-note">Mark one active level as the terminal level for graduation suggestions. Clear the current terminal level before selecting another.</p>
      {loading && <p role="status">Loading class levels…</p>}
      {error && <p className="setup-error" role="alert">{error}</p>}
      {message && <p className="academic-success" role="status">{message}</p>}
      {!loading && !hasLoaded && <button type="button" className="secondary-action" onClick={() => void retry()}>Retry</button>}
      {!loading && hasLoaded && (
        <>
          {canManage && (
            <form className="academic-form" onSubmit={add}>
              <h2>Add a level</h2>
              <div className="academic-form-grid">
                <label>Ghana catalogue level
                  <select name="catalogue_level" defaultValue="">
                    <option value="">Custom school level</option>
                    {unused.map((level) => <option key={level.code} value={level.code}>{level.name}</option>)}
                  </select>
                </label>
                <label>Display name (required for custom levels)
                  <input name="display_name" maxLength={80} />
                </label>
                <label>Sort order
                  <input name="sort_order" type="number" min="0" placeholder="Catalogue order" />
                </label>
              </div>
              <button className="setup-submit" disabled={busy} type="submit">Add level</button>
            </form>
          )}
          {levels.length === 0 ? <p className="academic-empty">No class levels configured yet.</p> : levels.map((level) => (
            <form className="academic-form" key={level.id} onSubmit={(event) => edit(event, level)}>
              <h2>{level.display_name}</h2>
              <p className="fin-note">{level.catalogue_name ? `Ghana catalogue: ${level.catalogue_name}` : "Custom school level"}</p>
              {canManage ? (
                <>
                  <div className="academic-form-grid">
                    <label>Display name<input name="display_name" defaultValue={level.display_name} required maxLength={80} /></label>
                    <label>Sort order<input name="sort_order" type="number" min="0" defaultValue={level.sort_order} required /></label>
                    <label><span className="academic-checkbox"><input name="is_active" type="checkbox" defaultChecked={level.is_active} />Active</span></label>
                    <label><span className="academic-checkbox"><input name="is_terminal" type="checkbox" defaultChecked={level.is_terminal} />Terminal graduation level</span></label>
                  </div>
                  <button className="setup-submit" disabled={busy} type="submit">Save {level.display_name}</button>
                </>
              ) : <p>{level.is_active ? "Active" : "Archived"}{level.is_terminal ? " · Terminal level" : ""}</p>}
            </form>
          ))}
        </>
      )}
    </section>
  );
}

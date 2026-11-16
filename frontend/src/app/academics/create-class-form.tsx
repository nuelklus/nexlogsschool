"use client";

import { FormEvent, useEffect, useState } from "react";

import type { AuthFetch, Campus } from "../school/student-api";
import { listClassLevels, type AcademicYear, type SchoolClassLevel } from "./academics-api";

export default function CreateClassForm({ authFetch, tenantId, years, campuses = [], onCreate }: {
  authFetch: AuthFetch;
  tenantId: string;
  years: AcademicYear[];
  campuses?: Campus[];
  onCreate: (data: Record<string, string>) => Promise<void>;
}) {
  const [levels, setLevels] = useState<SchoolClassLevel[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [levelId, setLevelId] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const activeCampuses = campuses.filter((campus) => campus.status === "active");

  useEffect(() => {
    let active = true;
    listClassLevels(authFetch, tenantId).then((result) => {
      if (active) {
        setLevels(result.filter((level) => level.is_active));
        setLoadError("");
      }
    }).catch((caught: unknown) => {
      if (active) setLoadError(caught instanceof Error ? caught.message : "Class levels could not be loaded.");
    });
    return () => { active = false; };
  }, [authFetch, tenantId, attempt]);

  function changeLevel(next: string) {
    const previousName = levels?.find((level) => level.id === levelId)?.display_name;
    const nextName = levels?.find((level) => level.id === next)?.display_name ?? "";
    if (!name.trim() || name === previousName) setName(nextName);
    setLevelId(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !levels || !levelId || !name.trim()) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await onCreate({
        academic_year: String(values.get("academic_year") ?? ""),
        name: name.trim(),
        section: String(values.get("section") ?? "").trim(),
        room: String(values.get("room") ?? "").trim(),
        campus: String(values.get("campus") ?? ""),
        ...(levelId === "unassigned" ? {} : { class_level: levelId }),
      });
      form.reset();
      setName("");
      setLevelId("");
      setMessage("Class created successfully.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The class could not be created.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form" onSubmit={submit}>
      <h2>Create class</h2>
      <div className="academic-form-grid">
        <label>Academic year
          <select name="academic_year" required defaultValue="">
            <option value="">Select academic year</option>
            {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
          </select>
        </label>
        <label>Class level
          <select value={levelId} required disabled={!levels} onChange={(event) => changeLevel(event.target.value)}>
            <option value="">Select class level</option>
            {levels?.map((level) => <option key={level.id} value={level.id}>{level.display_name}</option>)}
            <option value="unassigned">No level (assign later)</option>
          </select>
        </label>
        <label>Class name
          <input name="name" required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>Section<input name="section" maxLength={40} /></label>
        <label>Room<input name="room" maxLength={80} /></label>
          <label>Campus
            <select key={activeCampuses.map((campus) => campus.id).join(",")} name="campus" required defaultValue={activeCampuses.length === 1 ? activeCampuses[0].id : ""}>
              <option value="">Select campus</option>
              {activeCampuses.map((campus) => (
                <option key={campus.id} value={campus.id}>{campus.code} {campus.name}</option>
              ))}
            </select>
          </label>
      </div>
      <p className="fin-note">Choose the campus carefully: it cannot be changed after students enroll. Section and room are optional.</p>
      {activeCampuses.length === 0 && <p className="fin-note">Create an active campus in <a href="#campuses">Campuses</a> before creating a class.</p>}
      {!levels && !loadError && <p role="status">Loading class levels…</p>}
      {levels?.length === 0 && <p className="fin-note">No active levels are configured. Add one in Class levels, or choose No level to assign it later.</p>}
      {loadError && <><p className="setup-error" role="alert">{loadError}</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button></>}
      {error && <p className="setup-error" role="alert">{error}</p>}
      {message && <p className="academic-success" role="status">{message}</p>}
      <button className="setup-submit" type="submit" disabled={busy || !levels || activeCampuses.length === 0}>{busy ? "Saving…" : "Create class"}</button>
    </form>
  );
}

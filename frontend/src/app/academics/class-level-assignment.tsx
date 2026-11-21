"use client";

import { FormEvent, useEffect, useState } from "react";

import type { AuthFetch } from "../school/student-api";
import {
  assignClassLevel, listClassLevels,
  type AcademicYear, type SchoolClass, type SchoolClassLevel,
} from "./academics-api";

function ClassAssignment({ schoolClass, levels, year, authFetch, tenantId, onUpdated }: {
  schoolClass: SchoolClass;
  levels: SchoolClassLevel[];
  year: string;
  authFetch: AuthFetch;
  tenantId: string;
  onUpdated: (updated: SchoolClass) => void;
}) {
  const [selected, setSelected] = useState(schoolClass.class_level ?? "");
  const [saved, setSaved] = useState(schoolClass.class_level ?? "");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const label = `${schoolClass.name}${schoolClass.section ? ` · ${schoolClass.section}` : ""} · ${year}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await assignClassLevel(authFetch, tenantId, schoolClass.id, selected || null);
      setSaved(updated.class_level ?? "");
      setSelected(updated.class_level ?? "");
      onUpdated(updated);
      setMessage("Assignment saved.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The assignment could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form class-setup-item" onSubmit={submit}>
      <label>{label}
        <select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Not assigned</option>
          {levels.filter((level) => level.is_active || level.id === saved).map((level) => (
            <option key={level.id} value={level.id}>
              {level.display_name}{level.is_active ? "" : " (archived)"}
            </option>
          ))}
        </select>
      </label>
      {message && <p className="academic-success" role="status">{message}</p>}
      {error && <p className="setup-error" role="alert">{error}</p>}
      <button className="setup-submit" type="submit" disabled={busy || selected === saved}>
        Save level for {label}
      </button>
    </form>
  );
}

export default function ClassLevelAssignment({ authFetch, tenantId, classes, years, onUpdated }: {
  authFetch: AuthFetch;
  tenantId: string;
  classes: SchoolClass[];
  years: AcademicYear[];
  onUpdated: (updated: SchoolClass) => void;
}) {
  const [levels, setLevels] = useState<SchoolClassLevel[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    listClassLevels(authFetch, tenantId).then((result) => {
      if (active) {
        setLevels(result);
        setError("");
      }
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "Class levels could not be loaded.");
    });
    return () => { active = false; };
  }, [authFetch, tenantId, attempt]);

  return (
    <section className="class-setup-section" aria-label="Assign class levels">
      <h2>Assign levels to classes</h2>
      <p className="fin-note">Choose a level for each existing class. This does not change students, billing or class names.</p>
      {error && <><p className="setup-error" role="alert">{error}</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button></>}
      {!levels && !error && <p role="status">Loading class levels…</p>}
      {levels && classes.length === 0 && <p className="academic-empty">No classes have been added.</p>}
      {levels && classes.length > 0 && levels.length === 0 && <p className="academic-empty">Add a level in the Class levels tab first.</p>}
      {levels && levels.length > 0 && classes.map((schoolClass) => (
        <ClassAssignment
          key={schoolClass.id}
          schoolClass={schoolClass}
          levels={levels}
          year={years.find((item) => item.id === schoolClass.academic_year)?.name ?? "Year"}
          authFetch={authFetch}
          tenantId={tenantId}
          onUpdated={onUpdated}
        />
      ))}
    </section>
  );
}

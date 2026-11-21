"use client";

import { FormEvent, useState } from "react";

import type { AuthFetch, Campus } from "../school/student-api";
import { manageClass, type AcademicYear, type SchoolClass, type Teacher } from "./academics-api";

function ClassSettings({ schoolClass, year, teachers, campuses, authFetch, tenantId, onUpdated }: {
  schoolClass: SchoolClass;
  year: string;
  teachers: Teacher[];
  campuses: Campus[];
  authFetch: AuthFetch;
  tenantId: string;
  onUpdated: (updated: SchoolClass) => void;
}) {
  const [teacher, setTeacher] = useState(schoolClass.class_teacher ?? "");
  const [active, setActive] = useState(schoolClass.is_active !== false);
  const [campus, setCampus] = useState(schoolClass.campus ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const label = `${schoolClass.name}${schoolClass.section ? ` · ${schoolClass.section}` : ""} · ${year}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await manageClass(authFetch, tenantId, schoolClass.id, {
        ...(teacher !== (schoolClass.class_teacher ?? "") ? { class_teacher: teacher || null } : {}),
        ...(campus !== (schoolClass.campus ?? "") ? { campus: campus || null } : {}),
        is_active: active,
      });
      onUpdated(updated);
      setMessage("Class settings saved.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Class settings could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form class-setup-item" onSubmit={submit}>
      <h3>{label}</h3>
      <label>Class teacher for {label}
        <select value={teacher} onChange={(event) => setTeacher(event.target.value)}>
          <option value="">No class teacher</option>
          {teacher && !teachers.some((item) => item.id === teacher) && <option value={teacher}>Previously assigned teacher (inactive)</option>}
          {teachers.map((item) => <option value={item.id} key={item.id}>{item.name || item.email}</option>)}
        </select>
      </label>
      {campuses.length > 0 && (
        <label>Campus for {label}
          <select value={campus} onChange={(event) => setCampus(event.target.value)}>
            <option value="">No campus assigned</option>
            {campus && !campuses.some((item) => item.id === campus && item.status === "active") && <option value={campus}>Previously assigned campus (inactive)</option>}
            {campuses.filter((item) => item.status === "active").map((item) => (
              <option value={item.id} key={item.id}>{item.code} {item.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="academic-checkbox">
        <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
        Active class
      </label>
      {message && <p className="academic-success" role="status">{message}</p>}
      {error && <p className="setup-error" role="alert">{error}</p>}
      <button className="setup-submit" type="submit" disabled={busy}>Save settings for {label}</button>
    </form>
  );
}

export default function ClassManagement({ classes, years, teachers, campuses = [], authFetch, tenantId, onUpdated }: {
  classes: SchoolClass[];
  years: AcademicYear[];
  teachers: Teacher[];
  campuses?: Campus[];
  authFetch: AuthFetch;
  tenantId: string;
  onUpdated: (updated: SchoolClass) => void;
}) {
  return (
    <section className="class-setup-section" aria-label="Manage class groups">
      <h2>Manage class groups</h2>
      <p className="fin-note">Archiving keeps existing records but prevents new enrollments, transfers and promotions into the class.</p>
      {classes.map((schoolClass) => (
        <ClassSettings key={schoolClass.id} schoolClass={schoolClass}
          year={years.find((item) => item.id === schoolClass.academic_year)?.name ?? "Year"}
          teachers={teachers} campuses={campuses} authFetch={authFetch} tenantId={tenantId} onUpdated={onUpdated} />
      ))}
    </section>
  );
}

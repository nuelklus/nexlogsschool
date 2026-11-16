"use client";

import { useCallback, useEffect, useState } from "react";

import ClassLevelAssignment from "../academics/class-level-assignment";
import ClassLevelsPanel from "../academics/class-levels-panel";
import ClassManagement from "../academics/class-management";
import ClassReconciliation from "../academics/class-reconciliation";
import CreateClassForm from "../academics/create-class-form";
import {
  createAcademicRecord, listAcademicRecords,
  type AcademicYear, type SchoolClass, type Teacher,
} from "../academics/academics-api";
import { listCampuses, type AuthFetch, type Campus } from "./student-api";

function ClassGroups({ tenantId, authFetch, campusVersion }: { tenantId: string; authFetch: AuthFetch; campusVersion: number }) {
  const [data, setData] = useState<{
    years: AcademicYear[];
    classes: SchoolClass[];
    teachers: Teacher[];
    campuses: Campus[];
  } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(async () => {
    const [years, classes, teachers, campuses] = await Promise.all([
      listAcademicRecords(authFetch, tenantId, "years"),
      listAcademicRecords(authFetch, tenantId, "classes"),
      listAcademicRecords(authFetch, tenantId, "teachers"),
      listCampuses(authFetch, tenantId),
    ]);
    return { years, classes, teachers, campuses };
  }, [authFetch, tenantId]);

  useEffect(() => {
    let active = true;
    load().then((result) => {
      if (active) { setData(result); setError(""); }
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "Classes could not be loaded.");
    });
    return () => { active = false; };
  }, [load, attempt, campusVersion]);

  const updated = (schoolClass: SchoolClass) => setData((current) => current && ({
    ...current, classes: current.classes.map((item) => item.id === schoolClass.id ? schoolClass : item),
  }));

  return (
    <div>
      {error && <><p className="setup-error" role="alert">{error}</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button></>}
      {!data && !error && <p role="status">Loading classes…</p>}
      {data && <>
        <CreateClassForm authFetch={authFetch} tenantId={tenantId} years={data.years} campuses={data.campuses} onCreate={async (values) => {
          await createAcademicRecord(authFetch, tenantId, "classes", values);
          try {
            setData(await load());
            setError("");
          } catch (caught) {
            setData(null);
            setError(`The class was created, but refreshing failed. Retry before creating another class. ${caught instanceof Error ? caught.message : "Classes could not be loaded."}`);
          }
        }} />
        <h2>Classes</h2>
        {data.classes.length === 0 ? <p className="academic-empty">No classes have been added.</p> : (
          <ul className="academic-records">
            {data.classes.map((item) => <li key={item.id}>
              <strong>{item.name}{item.section ? ` · ${item.section}` : ""}</strong>
              <span>{data.years.find((year) => year.id === item.academic_year)?.name ?? "Year"} · {data.campuses.find((campus) => campus.id === item.campus)?.name ?? "No campus assigned"}{item.room ? ` · Room ${item.room}` : ""}{item.is_active === false ? " · Archived" : ""}</span>
            </li>)}
          </ul>
        )}
        <ClassManagement authFetch={authFetch} tenantId={tenantId} classes={data.classes} years={data.years} teachers={data.teachers} campuses={data.campuses} onUpdated={updated} />
        <ClassLevelAssignment authFetch={authFetch} tenantId={tenantId} classes={data.classes} years={data.years} onUpdated={updated} />
        <ClassReconciliation authFetch={authFetch} tenantId={tenantId} />
      </>}
    </div>
  );
}

export default function ClassSetupPanel({ tenantId, authFetch, campusVersion = 0 }: { tenantId: string; authFetch: AuthFetch; campusVersion?: number }) {
  const [active, setActive] = useState<"levels" | "classes">("levels");
  return (
    <section className="academic-card" id="class-setup" aria-label="School class setup">
      <p className="eyebrow">SCHOOL SETUP</p>
      <h2>Class levels &amp; classes</h2>
      <p className="fin-note">Set up levels and class groups for use across your school.</p>
      <nav className="finance-tabs" aria-label="Class setup sections">
        <button type="button" className={active === "levels" ? "active" : ""} aria-pressed={active === "levels"} onClick={() => setActive("levels")}>Class levels</button>
        <button type="button" className={active === "classes" ? "active" : ""} aria-pressed={active === "classes"} onClick={() => setActive("classes")}>Classes</button>
      </nav>
      {active === "levels" ? <ClassLevelsPanel authFetch={authFetch} tenantId={tenantId} canManage />
        : <ClassGroups tenantId={tenantId} authFetch={authFetch} campusVersion={campusVersion} />}
    </section>
  );
}

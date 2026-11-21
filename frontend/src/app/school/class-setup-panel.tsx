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
        <section className="class-setup-section class-setup-directory" aria-label="Class list">
          <div className="class-setup-item-heading">
            <h2>Class list</h2>
            <span className="class-setup-badge">{data.classes.length} {data.classes.length === 1 ? "class" : "classes"}</span>
          </div>
          {data.classes.length === 0 ? <p className="academic-empty">No classes have been added yet. Create a class above to get started.</p> : (
            <ul className="academic-records">
              {data.classes.map((item) => <li key={item.id}>
                <div className="class-setup-item-heading">
                  <strong>{item.name}{item.section ? ` · ${item.section}` : ""}</strong>
                  <span className={`class-setup-badge${item.is_active === false ? " is-muted" : ""}`}>{item.is_active === false ? "Archived" : "Active"}</span>
                </div>
                <span>{data.years.find((year) => year.id === item.academic_year)?.name ?? "Year"} · {data.campuses.find((campus) => campus.id === item.campus)?.name ?? "No campus assigned"}{item.room ? ` · Room ${item.room}` : ""}</span>
              </li>)}
            </ul>
          )}
        </section>
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
    <section className="academic-card class-setup-panel" id="class-setup" aria-label="School class setup">
      <div className="class-setup-intro">
        <p className="eyebrow">SCHOOL SETUP</p>
        <h2>Class levels &amp; classes</h2>
        <p className="fin-note">Organize the levels your school offers and the class groups students can join.</p>
        <ol className="class-setup-steps" aria-label="Recommended setup order">
          <li><strong>1. Set up levels</strong><span>Choose or name the levels your school offers.</span></li>
          <li><strong>2. Create classes</strong><span>Choose an academic year and campus for each class.</span></li>
          <li><strong>3. Assign and manage</strong><span>Link classes to levels and manage teachers or active status.</span></li>
        </ol>
      </div>
      <nav className="academic-tabs" aria-label="Class setup sections">
        <button type="button" className={active === "levels" ? "active" : ""} aria-pressed={active === "levels"} onClick={() => setActive("levels")}>Class levels</button>
        <button type="button" className={active === "classes" ? "active" : ""} aria-pressed={active === "classes"} onClick={() => setActive("classes")}>Classes</button>
      </nav>
      {active === "levels" ? <ClassLevelsPanel authFetch={authFetch} tenantId={tenantId} canManage />
        : <ClassGroups tenantId={tenantId} authFetch={authFetch} campusVersion={campusVersion} />}
    </section>
  );
}

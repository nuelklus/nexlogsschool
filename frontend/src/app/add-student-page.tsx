"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { AcademicYear, SchoolClass, listAcademicRecords } from "./academics/academics-api";
import AuthControls from "./auth-controls";
import { AuthMembership, useAuth } from "./auth-provider";
import { InviteForm, useLoadFailure } from "./school/school-dashboard";
import StudentDetailsModal from "./school/student-details-modal";
import {
  MANAGE_ROLES,
  AuthFetch,
  Campus,
  Student,
  enrollStudent,
  inviteGuardian,
  invitePortal,
  listCampuses,
  listStudents,
} from "./school/student-api";
import { ExpiredNotice } from "./school/subscription-panel";
import WorkspaceSidebar from "./workspace-sidebar";

const initialsOf = (student: Student) =>
  `${student.first_name[0] ?? ""}${student.last_name[0] ?? ""}`.toUpperCase();

const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function EnrolForm({ onEnrol, authFetch, tenantId }: { onEnrol: (data: FormData) => Promise<Student>; authFetch: AuthFetch; tenantId: string }) {
  const [years, setYears] = useState<AcademicYear[] | null>(null);
  const [classes, setClasses] = useState<SchoolClass[] | null>(null);
  const [campuses, setCampuses] = useState<Campus[] | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [yearId, setYearId] = useState("");
  const [campusId, setCampusId] = useState("");
  const [classId, setClassId] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([
      listAcademicRecords(authFetch, tenantId, "years"),
      listAcademicRecords(authFetch, tenantId, "classes"),
      listCampuses(authFetch, tenantId),
    ]).then(([loadedYears, loadedClasses, loadedCampuses]) => {
      if (!active) return;
      const availableYears = loadedYears.filter((year) => year.status === "active");
      setYears(availableYears);
      setClasses(loadedClasses.filter((schoolClass) => schoolClass.is_active !== false));
      setCampuses(loadedCampuses);
      setYearId(availableYears.find((year) => year.is_current)?.id ?? availableYears[0]?.id ?? "");
      setOptionsError("");
    }).catch((error: unknown) => {
      if (active) setOptionsError(error instanceof Error ? error.message : "Admission options could not be loaded.");
    });
    return () => { active = false; };
  }, [authFetch, tenantId]);

  const availableClasses = (classes ?? []).filter((schoolClass) =>
    schoolClass.academic_year === yearId
      && (!campusId || schoolClass.campus === campusId),
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    setBusy(true);
    setMessage("");
    try {
      const created = await onEnrol(new FormData(form));
      setMessage(
        `${created.first_name} ${created.last_name} has been enrolled. Admission number: ${created.admission_number}`,
      );
      setFailed(false);
      setCampusId("");
      setClassId("");
      form.reset();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Something went wrong. Try again.");
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="enrol-card" onSubmit={submit} aria-label="Enrol a student">
      <div className="enrol-card-head">
        <div>
          <p className="eyebrow">NEW STUDENT</p>
          <h2>Enrol a student</h2>
        </div>
      </div>
      <fieldset className="enrol-fieldset">
        <legend>Identity</legend>
        <div className="enrol-grid">
          <div className="enrol-wide enrol-auto-number">
            <span>Admission number</span>
            <strong>Generated automatically</strong>
            <small>It is assigned when you enrol the student and cannot be typed in.</small>
          </div>
          <label>First name<input name="first_name" maxLength={100} required autoComplete="off" /></label>
          <label>Last name<input name="last_name" maxLength={100} required autoComplete="off" /></label>
        </div>
      </fieldset>
      <fieldset className="enrol-fieldset">
        <legend>Details</legend>
        <div className="enrol-grid">
          <label>Date of birth<input name="date_of_birth" type="date" /></label>
          <label>Gender
            <select name="gender" defaultValue="">
              <option value="">Select gender (optional)</option>
              <option value="Female">Female</option>
              <option value="Male">Male</option>
              <option value="Other">Other</option>
              <option value="Prefer not to say">Prefer not to say</option>
            </select>
          </label>
          <label>Academic year
            <select name="initial_academic_year" required value={yearId} onChange={(event) => {
              setYearId(event.target.value);
              setClassId("");
            }} disabled={!years?.length}>
              <option value="" disabled>Select academic year</option>
              {years?.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
            </select>
          </label>
          {(campuses?.length ?? 0) > 0 && (
            <label>Campus
              <select name="campus" value={campusId} onChange={(event) => {
                setCampusId(event.target.value);
                setClassId("");
              }}>
                <option value="">Select campus (optional)</option>
                {campuses?.filter((campus) => campus.status === "active").map((campus) => (
                  <option key={campus.id} value={campus.id}>{campus.code} {campus.name}</option>
                ))}
              </select>
            </label>
          )}
          <label>Class group
            <select
              name="initial_school_class"
              required
              value={classId}
              onChange={(event) => setClassId(event.target.value)}
              disabled={!classes}
            >
              <option value="">Select class group</option>
              {availableClasses.map((schoolClass) => (
                <option key={schoolClass.id} value={schoolClass.id}>
                  {schoolClass.name}{schoolClass.section ? ` · ${schoolClass.section}` : ""} · {campuses?.find((campus) => campus.id === schoolClass.campus)?.name ?? "No campus assigned"}
                </option>
              ))}
            </select>
          </label>
          <label>Enrollment date<input name="enrolled_on" type="date" required /></label>
        </div>
        <p className="fin-note">The class campus is recorded on the enrollment. Classes without a campus cannot support official report publication.</p>
        {optionsError && <p className="setup-error" role="alert">{optionsError}</p>}
        {!optionsError && years && !years.length && (
          <p className="fin-note">Create an active academic year before admitting students.</p>
        )}
        {!optionsError && classes && !availableClasses.length && (
          <p className="fin-note">Create a class group in the selected academic year before admitting students.</p>
        )}
      </fieldset>
      {message && (
        <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>
          {message}
        </p>
      )}
      <div className="enrol-actions">
        <button className="setup-submit" type="submit" disabled={busy || !years?.length || !availableClasses.length}>
          {busy ? "Enrolling…" : "Enrol student"}
        </button>
      </div>
    </form>
  );
}

function StudentRow({
  student,
  className,
  onDetails,
  onGuardian,
  onPortal,
}: {
  student: Student;
  className?: string;
  onDetails: (student: Student, trigger: HTMLButtonElement) => void;
  onGuardian: (student: Student, data: FormData) => Promise<string>;
  onPortal: (student: Student, data: FormData) => Promise<string>;
}) {
  return (
    <li className="roster-row">
      <div className="roster-main">
        <span className="roster-avatar" aria-hidden="true">{initialsOf(student)}</span>
        <button type="button" className="roster-name roster-details-button" onClick={(event) => onDetails(student, event.currentTarget)} aria-label={`View details for ${student.first_name} ${student.last_name}, ${student.admission_number}`}>
          <strong>{student.first_name} {student.last_name}</strong>
          <span>
            {student.admission_number}
            {className || student.grade_level ? ` · ${className || student.grade_level}` : ""}
          </span>
        </button>
        <span className={`state-pill ${student.status === "active" ? "ready" : "pending"}`}>
          <span className="state-dot" />
          {student.status}
        </span>
        {student.portal_account && <span className="roster-tag">Portal active</span>}
      </div>
      <details className="roster-invites">
        <summary>Invite family or student</summary>
        <div className="roster-invite-body">
          <InviteForm
            label="Invite guardian"
            withRelationship
            onSubmit={(data) => onGuardian(student, data)}
          />
          {!student.portal_account && (
            <InviteForm
              label="Invite student portal"
              withRelationship={false}
              onSubmit={(data) => onPortal(student, data)}
            />
          )}
        </div>
      </details>
    </li>
  );
}

function Page({
  memberships,
  membership,
  onSelect,
}: {
  memberships: AuthMembership[];
  membership: AuthMembership;
  onSelect: (tenantId: string) => void;
}) {
  const { authFetch } = useAuth();
  const tenantId = membership.tenant_id;
  const [students, setStudents] = useState<Student[] | null>(null);
  const [query, setQuery] = useState("");
  const [selectedStudent, setSelectedStudent] = useState<{ student: Student; trigger: HTMLButtonElement } | null>(null);
  const [rosterClasses, setRosterClasses] = useState<SchoolClass[] | null>(null);
  const [rosterCampuses, setRosterCampuses] = useState<Campus[] | null>(null);
  const [filterError, setFilterError] = useState("");
  const [classFilter, setClassFilter] = useState("");
  const [campusFilter, setCampusFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [genderFilter, setGenderFilter] = useState("");
  const [portalFilter, setPortalFilter] = useState("");
  const { error, setError, expired, onFailure } = useLoadFailure();

  useEffect(() => {
    let current = true;
    listStudents(authFetch, tenantId)
      .then((list) => current && (setStudents(list), setError("")))
      .catch((caught) => current && onFailure(caught));
    return () => {
      current = false;
    };
  }, [authFetch, tenantId, setError, onFailure]);

  useEffect(() => {
    let active = true;
    Promise.all([
      listAcademicRecords(authFetch, tenantId, "years"),
      listAcademicRecords(authFetch, tenantId, "classes"),
      listCampuses(authFetch, tenantId),
    ]).then(([years, classes, campuses]) => {
      if (!active) return;
      const currentYear = years.find((year) => year.is_current);
      setRosterClasses(classes.filter((item) => item.academic_year === currentYear?.id));
      setRosterCampuses(campuses);
    }).catch((caught: unknown) => {
      if (active) setFilterError(caught instanceof Error ? caught.message : "Could not load filter options.");
    });
    return () => { active = false; };
  }, [authFetch, tenantId]);

  const enrol = useCallback(
    async (data: FormData) => {
      const created = await enrollStudent(authFetch, tenantId, {
        first_name: text(data, "first_name"),
        last_name: text(data, "last_name"),
        date_of_birth: text(data, "date_of_birth"),
        gender: text(data, "gender"),
        enrolled_on: text(data, "enrolled_on"),
        initial_academic_year: text(data, "initial_academic_year"),
        initial_school_class: text(data, "initial_school_class"),
        campus: text(data, "campus"),
      });
      setStudents((existing) => [...(existing ?? []), created]);
      return created;
    },
    [authFetch, tenantId],
  );

  const guardian = useCallback(
    async (student: Student, data: FormData) => {
      const result = await inviteGuardian(authFetch, tenantId, student.id, {
        email: text(data, "email"),
        relationship: text(data, "relationship"),
        is_primary: data.get("is_primary") === "on",
      });
      return result.status === "linked"
        ? `${result.email} was linked to ${student.first_name}.`
        : `Invitation sent to ${result.email}.`;
    },
    [authFetch, tenantId],
  );

  const portal = useCallback(
    async (student: Student, data: FormData) => {
      const result = await invitePortal(authFetch, tenantId, student.id, text(data, "email"));
      return `Portal invitation sent to ${result.email}.`;
    },
    [authFetch, tenantId],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!students) return [];
    return students.filter((student) =>
      (!needle || `${student.first_name} ${student.last_name} ${student.admission_number} ${student.grade_level}`
        .toLowerCase()
        .includes(needle))
      && (!classFilter || (classFilter === "__none__" ? !student.current_class : student.current_class === classFilter))
      && (!campusFilter || (campusFilter === "__none__" ? !student.campus : student.campus === campusFilter))
      && (!statusFilter || student.status === statusFilter)
      && (!genderFilter || (genderFilter === "__none__" ? !student.gender : student.gender === genderFilter))
      && (!portalFilter || student.portal_account === (portalFilter === "active")),
    );
  }, [students, query, classFilter, campusFilter, statusFilter, genderFilter, portalFilter]);
  const genders = [...new Set((students ?? []).map((student) => student.gender).filter(Boolean))].sort();
  function clearFilters() {
    setQuery(""); setClassFilter(""); setCampusFilter(""); setStatusFilter(""); setGenderFilter(""); setPortalFilter("");
  }

  const total = students?.length ?? null;
  const active = students?.filter((student) => student.status === "active").length ?? null;
  const withPortal = students?.filter((student) => student.portal_account).length ?? null;

  return (
    <main className="workspace">
      <WorkspaceSidebar
        memberships={memberships}
        membership={membership}
        onSelect={onSelect}
        active="students"
      />
      <section className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Students</div>
          <div className="topbar-actions"><AuthControls /></div>
        </header>
        <div className="content">
          <section className="page-heading">
            <div>
              <p className="eyebrow">ENROLMENT</p>
              <h1>Students</h1>
              <p className="heading-copy">
                Enrol students at {membership.tenant_name}, then invite their parents or guardians and
                give students access to their own profile.
              </p>
            </div>
          </section>

          {expired ? (
            <ExpiredNotice message={expired} canManage tenantId={tenantId} authFetch={authFetch} />
          ) : (
            <>
              <section className="stats-row" aria-label="Student summary">
                <article className="stat-block"><span className="stat-label">ENROLLED</span><strong>{total ?? "—"}</strong><span className="stat-note">total students</span></article>
                <article className="stat-block"><span className="stat-label">ACTIVE</span><strong>{active ?? "—"}</strong><span className="stat-note">currently attending</span></article>
                <article className="stat-block"><span className="stat-label">PORTAL ACCESS</span><strong>{withPortal ?? "—"}</strong><span className="stat-note">students with an account</span></article>
              </section>

              {error && <p className="setup-error" role="alert">{error}</p>}

              <div className="enrol-layout">
                <EnrolForm onEnrol={enrol} authFetch={authFetch} tenantId={tenantId} />
                <section className="enrol-card roster" aria-label="Student roster">
                  <div className="enrol-card-head">
                    <div>
                      <p className="eyebrow">ROSTER</p>
                      <h2>All students</h2>
                    </div>
                    <input
                      className="roster-search"
                      type="search"
                      placeholder="Search by name, number or grade"
                      aria-label="Search students"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </div>
                  {filterError && <p className="setup-error" role="alert">Class and campus filter options could not be loaded: {filterError}</p>}
                  <div className="roster-filters" aria-label="Student filters">
                    <label>Filter by class group <select value={classFilter} disabled={!rosterClasses} onChange={(event) => setClassFilter(event.target.value)}>
                      <option value="">All class groups</option>
                      {rosterClasses?.map((item) => <option key={item.id} value={item.id}>{item.name}{item.section ? ` ${item.section}` : ""}{item.is_active === false ? " (archived)" : ""}</option>)}
                      <option value="__none__">No unique current class</option>
                    </select></label>
                    <label>Filter by campus <select value={campusFilter} disabled={!rosterCampuses} onChange={(event) => setCampusFilter(event.target.value)}>
                      <option value="">All campuses</option>
                      {rosterCampuses?.map((campus) => <option key={campus.id} value={campus.id}>{campus.name}{campus.status !== "active" ? " (inactive)" : ""}</option>)}
                      <option value="__none__">No campus</option>
                    </select></label>
                    <label>Filter by status <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                      <option value="">All statuses</option><option value="active">Active</option><option value="withdrawn">Withdrawn</option><option value="graduated">Graduated</option>
                    </select></label>
                    <label>Filter by gender <select value={genderFilter} onChange={(event) => setGenderFilter(event.target.value)}>
                      <option value="">All genders</option>{genders.map((gender) => <option key={gender} value={gender}>{gender}</option>)}<option value="__none__">Not specified</option>
                    </select></label>
                    <label>Filter by portal access <select value={portalFilter} onChange={(event) => setPortalFilter(event.target.value)}>
                      <option value="">All students</option><option value="active">Account active</option><option value="inactive">No account</option>
                    </select></label>
                    <button type="button" onClick={clearFilters}>Clear filters</button>
                  </div>
                  <p className="fin-note">Class groups reflect unique active enrollments in the current academic year; students without one are listed separately. Campus may use the student profile when enrollment evidence is unavailable.</p>
                  {students !== null && <p className="fin-note" aria-live="polite">Showing {visible.length} of {students.length} students.</p>}
                  {students === null ? (
                    !error && <p role="status" className="roster-empty">Loading students…</p>
                  ) : students?.length === 0 ? (
                    <p className="roster-empty">No students are enrolled yet. Use the form to add the first one.</p>
                  ) : visible.length === 0 ? (
                    <p className="roster-empty">No students match your search or filters.</p>
                  ) : (
                    <ul className="roster-list">
                      {visible.map((student) => {
                        const schoolClass = rosterClasses?.find((item) => item.id === student.current_class);
                        return <StudentRow key={student.id} student={student} className={schoolClass ? [schoolClass.name, schoolClass.section].filter(Boolean).join(" ") : undefined} onDetails={(item, trigger) => setSelectedStudent({ student: item, trigger })} onGuardian={guardian} onPortal={portal} />;
                      })}
                    </ul>
                  )}
                </section>
              </div>
              {selectedStudent && <StudentDetailsModal key={selectedStudent.student.id} student={selectedStudent.student} returnFocus={selectedStudent.trigger}
                schoolClass={rosterClasses?.find((item) => item.id === selectedStudent.student.current_class) ?? null}
                campus={rosterCampuses?.find((item) => item.id === selectedStudent.student.campus) ?? null}
                authFetch={authFetch} tenantId={tenantId} onClose={() => setSelectedStudent(null)} />}
            </>
          )}
        </div>
      </section>
    </main>
  );
}

// Owners and school administrators only; everyone else goes back to their own view.
export default function AddStudentPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [selected, setSelected] = useState("");
  const managed = user?.memberships.filter((item) => MANAGE_ROLES.includes(item.role)) ?? [];
  const allowed = managed.length > 0;

  useEffect(() => {
    if (user && !allowed) router.replace("/");
  }, [user, allowed, router]);

  if (!user || !allowed) return null;
  const membership = managed.find((item) => item.tenant_id === selected) ?? managed[0];
  return (
    <Page
      key={membership.tenant_id}
      memberships={managed}
      membership={membership}
      onSelect={setSelected}
    />
  );
}

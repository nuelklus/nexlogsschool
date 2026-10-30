"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import AuthControls from "./auth-controls";
import { AuthMembership, useAuth } from "./auth-provider";
import { GRADE_LEVELS } from "./grade-levels";
import { InviteForm, useLoadFailure } from "./school/school-dashboard";
import {
  MANAGE_ROLES,
  Student,
  enrollStudent,
  inviteGuardian,
  invitePortal,
  listStudents,
} from "./school/student-api";
import { ExpiredNotice } from "./school/subscription-panel";
import WorkspaceSidebar from "./workspace-sidebar";

const initialsOf = (student: Student) =>
  `${student.first_name[0] ?? ""}${student.last_name[0] ?? ""}`.toUpperCase();

const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function EnrolForm({ onEnrol }: { onEnrol: (data: FormData) => Promise<Student> }) {
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

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
          <label>Gender<input name="gender" maxLength={20} /></label>
          <label>Class
            <select name="grade_level" defaultValue="">
              <option value="">Select class</option>
              {GRADE_LEVELS.map((level) => <option key={level} value={level}>{level}</option>)}
            </select>
          </label>
        </div>
      </fieldset>
      {message && (
        <p className={failed ? "setup-error" : "enrol-success"} role={failed ? "alert" : "status"}>
          {message}
        </p>
      )}
      <div className="enrol-actions">
        <button className="setup-submit" type="submit" disabled={busy}>
          {busy ? "Enrolling…" : "Enrol student"}
        </button>
      </div>
    </form>
  );
}

function StudentRow({
  student,
  onGuardian,
  onPortal,
}: {
  student: Student;
  onGuardian: (student: Student, data: FormData) => Promise<string>;
  onPortal: (student: Student, data: FormData) => Promise<string>;
}) {
  return (
    <li className="roster-row">
      <div className="roster-main">
        <span className="roster-avatar" aria-hidden="true">{initialsOf(student)}</span>
        <div className="roster-name">
          <strong>{student.first_name} {student.last_name}</strong>
          <span>
            {student.admission_number}
            {student.grade_level ? ` · ${student.grade_level}` : ""}
          </span>
        </div>
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

  const enrol = useCallback(
    async (data: FormData) => {
      const created = await enrollStudent(authFetch, tenantId, {
        first_name: text(data, "first_name"),
        last_name: text(data, "last_name"),
        date_of_birth: text(data, "date_of_birth"),
        gender: text(data, "gender"),
        grade_level: text(data, "grade_level"),
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
    if (!needle) return students;
    return students.filter((student) =>
      `${student.first_name} ${student.last_name} ${student.admission_number} ${student.grade_level}`
        .toLowerCase()
        .includes(needle),
    );
  }, [students, query]);

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
                <EnrolForm onEnrol={enrol} />
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
                  {students === null && !error ? (
                    <p role="status" className="roster-empty">Loading students…</p>
                  ) : students?.length === 0 ? (
                    <p className="roster-empty">No students are enrolled yet. Use the form to add the first one.</p>
                  ) : visible.length === 0 ? (
                    <p className="roster-empty">No students match your search.</p>
                  ) : (
                    <ul className="roster-list">
                      {visible.map((student) => (
                        <StudentRow key={student.id} student={student} onGuardian={guardian} onPortal={portal} />
                      ))}
                    </ul>
                  )}
                </section>
              </div>
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

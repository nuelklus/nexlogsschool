"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { AuthMembership, useAuth } from "../auth-provider";
import {
  AuthFetch,
  Child,
  MANAGE_ROLES,
  READ_ROLES,
  Student,
  getOwnStudent,
  inviteGuardian,
  invitePortal,
  listChildren,
  listStudents,
  SubscriptionExpiredError,
} from "./student-api";
import { ExpiredNotice } from "./subscription-panel";
import PublishedResults from "./published-results";
import PublishedReportCards from "./published-report-cards";

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong. Try again.";

// A 402 means the subscription lapsed; it gets its own page instead of a generic error.
export function useLoadFailure() {
  const [error, setError] = useState("");
  const [expired, setExpired] = useState("");
  const onFailure = useCallback((caught: unknown) => {
    if (caught instanceof SubscriptionExpiredError) setExpired(caught.message);
    else setError(errorText(caught));
  }, []);
  return { error, setError, expired, onFailure };
}

function SignOutButton() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <div className="auth-controls">
      <Link className="setup-back" href="/">Overview</Link>
      <span className="auth-control-muted">{user.full_name}</span>
      <button className="auth-signout" type="button" onClick={() => void signOut()}>Sign out</button>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="setup-page">
      <div className="setup-shell school-shell">
        <header className="setup-header">
          <Link className="setup-brand" href="/">
            <span className="setup-brand-mark">N</span>
            <span>NLSchool</span>
          </Link>
          <SignOutButton />
        </header>
        {children}
      </div>
    </main>
  );
}

function StudentName({ student }: { student: Student }) {
  return (
    <>
      <strong>{student.first_name} {student.last_name}</strong>
      <span>
        {student.admission_number}
        {student.grade_level ? ` · ${student.grade_level}` : ""} · {student.status}
      </span>
    </>
  );
}

export function InviteForm({
  label,
  withRelationship,
  onSubmit,
}: {
  label: string;
  withRelationship: boolean;
  onSubmit: (data: FormData) => Promise<string>;
}) {
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
      setMessage(await onSubmit(new FormData(form)));
      setFailed(false);
      form.reset();
    } catch (error) {
      setMessage(errorText(error));
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="school-invite" onSubmit={submit} aria-label={label}>
      <input name="email" type="email" placeholder="Email address" aria-label={`${label} email`} required />
      {withRelationship && (
        <>
          <input name="relationship" placeholder="Relationship" aria-label={`${label} relationship`} maxLength={40} />
          <label className="school-check">
            <input name="is_primary" type="checkbox" /> Primary
          </label>
        </>
      )}
      <button className="auth-signout" type="submit" disabled={busy}>{busy ? "Sending…" : label}</button>
      {message && <p className={failed ? "setup-error" : "school-note"} role={failed ? "alert" : "status"}>{message}</p>}
    </form>
  );
}

function StaffView({
  membership,
  authFetch,
}: {
  membership: AuthMembership;
  authFetch: AuthFetch;
}) {
  const tenantId = membership.tenant_id;
  const canManage = MANAGE_ROLES.includes(membership.role);
  const [students, setStudents] = useState<Student[] | null>(null);
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

  if (expired) {
    return <ExpiredNotice message={expired} canManage={canManage} tenantId={tenantId} authFetch={authFetch} />;
  }

  return (
    <>
      {error && <p className="setup-error" role="alert">{error}</p>}
      {canManage && (
        <p className="school-note">
          Need to admit a student? <Link href="/dashboard-add-student">Open the admission form</Link>.
        </p>
      )}
      <h2>Students</h2>
      {students === null && !error ? (
        <p role="status">Loading students…</p>
      ) : students?.length === 0 ? (
        <p>No students are enrolled yet.</p>
      ) : (
        <ul className="school-list">
          {students?.map((student) => (
            <li key={student.id}>
              <div><StudentName student={student} /></div>
              {canManage && (
                <div className="school-actions">
                  <InviteForm
                    label="Invite guardian"
                    withRelationship
                    onSubmit={async (data) => {
                      const result = await inviteGuardian(authFetch, tenantId, student.id, {
                        email: String(data.get("email") ?? "").trim(),
                        relationship: String(data.get("relationship") ?? "").trim(),
                        is_primary: data.get("is_primary") === "on",
                      });
                      return result.status === "linked"
                        ? `${result.email} was linked to ${student.first_name}.`
                        : `Invitation sent to ${result.email}.`;
                    }}
                  />
                  {student.portal_account ? (
                    <p className="school-note">Student portal active</p>
                  ) : (
                    <InviteForm
                      label="Invite student portal"
                      withRelationship={false}
                      onSubmit={async (data) => {
                        const result = await invitePortal(
                          authFetch,
                          tenantId,
                          student.id,
                          String(data.get("email") ?? "").trim(),
                        );
                        return `Portal invitation sent to ${result.email}.`;
                      }}
                    />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function ParentView({ membership, authFetch }: { membership: AuthMembership; authFetch: AuthFetch }) {
  const [children, setChildren] = useState<Child[] | null>(null);
  const { error, setError, expired, onFailure } = useLoadFailure();

  useEffect(() => {
    let current = true;
    listChildren(authFetch, membership.tenant_id)
      .then((list) => current && (setChildren(list), setError("")))
      .catch((caught) => current && onFailure(caught));
    return () => {
      current = false;
    };
  }, [authFetch, membership.tenant_id, setError, onFailure]);

  if (expired) {
    return <ExpiredNotice message={expired} canManage={false} tenantId={membership.tenant_id} authFetch={authFetch} />;
  }

  return (
    <>
      <h2>Your children</h2>
      {error && <p className="setup-error" role="alert">{error}</p>}
      {children === null && !error ? (
        <p role="status">Loading…</p>
      ) : children?.length === 0 ? (
        <p>No children are linked to your account yet. Ask the school to send a guardian invitation.</p>
      ) : (
        <ul className="school-list">
          {children?.map((child) => (
            <li key={child.id}>
              <div>
                <StudentName student={child} />
                {child.relationship && <span>{child.relationship}{child.is_primary ? " · primary guardian" : ""}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <PublishedResults tenantId={membership.tenant_id} authFetch={authFetch} />
      <PublishedReportCards tenantId={membership.tenant_id} authFetch={authFetch} />
    </>
  );
}

function StudentView({ membership, authFetch }: { membership: AuthMembership; authFetch: AuthFetch }) {
  const [student, setStudent] = useState<Student | null>(null);
  const { error, setError, expired, onFailure } = useLoadFailure();

  useEffect(() => {
    let current = true;
    getOwnStudent(authFetch, membership.tenant_id)
      .then((record) => current && (setStudent(record), setError("")))
      .catch((caught) => current && onFailure(caught));
    return () => {
      current = false;
    };
  }, [authFetch, membership.tenant_id, setError, onFailure]);

  if (expired) {
    return <ExpiredNotice message={expired} canManage={false} tenantId={membership.tenant_id} authFetch={authFetch} />;
  }

  return (
    <>
      <h2>Your student profile</h2>
      {error && <p className="setup-error" role="alert">{error}</p>}
      {student ? (
        <ul className="school-list">
          <li><div><StudentName student={student} /></div></li>
        </ul>
      ) : (
        !error && <p role="status">Loading…</p>
      )}
      <PublishedResults tenantId={membership.tenant_id} authFetch={authFetch} />
      <PublishedReportCards tenantId={membership.tenant_id} authFetch={authFetch} />
    </>
  );
}

export default function SchoolDashboard() {
  const { user, isLoading, authFetch } = useAuth();
  const [selected, setSelected] = useState("");

  if (isLoading) {
    return <Shell><section className="setup-card"><p role="status">Checking your session…</p></section></Shell>;
  }
  if (!user) {
    return (
      <Shell>
        <section className="setup-card">
          <h1>Sign in to see your school.</h1>
          <Link className="setup-submit" href="/login">Sign in <span aria-hidden="true">→</span></Link>
        </section>
      </Shell>
    );
  }
  if (user.memberships.length === 0) {
    return <Shell><section className="setup-card"><p>This account has no active school membership yet.</p></section></Shell>;
  }

  const membership =
    user.memberships.find((item) => item.tenant_id === selected) ?? user.memberships[0];
  const { role } = membership;

  return (
    <Shell>
      <section className="setup-card">
        <div className="setup-step">
          <span>{membership.tenant_name.toUpperCase()}</span>
          <span>{role.replaceAll("_", " ").toUpperCase()}</span>
        </div>
        {READ_ROLES.includes(role) && (
          <p className="school-note">
            <Link href="/academics">Open Academic Management <span aria-hidden="true">→</span></Link>
          </p>
        )}
        {user.memberships.length > 1 && (
          <label className="school-switch">
            School
            <select value={membership.tenant_id} onChange={(event) => setSelected(event.target.value)}>
              {user.memberships.map((item) => (
                <option key={item.tenant_id} value={item.tenant_id}>
                  {item.tenant_name} ({item.role.replaceAll("_", " ")})
                </option>
              ))}
            </select>
          </label>
        )}
        {READ_ROLES.includes(role) ? (
          <StaffView key={membership.tenant_id} membership={membership} authFetch={authFetch} />
        ) : role === "parent" ? (
          <ParentView key={membership.tenant_id} membership={membership} authFetch={authFetch} />
        ) : role === "student" ? (
          <StudentView key={membership.tenant_id} membership={membership} authFetch={authFetch} />
        ) : (
          <p>Your role does not have a workspace yet.</p>
        )}
      </section>
    </Shell>
  );
}

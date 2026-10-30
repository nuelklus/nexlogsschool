"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AuthControls from "./auth-controls";
import { AuthMembership, useAuth } from "./auth-provider";
import { StaffInvitePanel } from "./school/invite-panel";
import SchoolDashboard from "./school/school-dashboard";
import { listStudents, MANAGE_ROLES } from "./school/student-api";
import { SubscriptionPanel } from "./school/subscription-panel";
import {
  SubscriptionStatus,
  formatDate,
  getSubscription,
  intervalLabel,
  statusLabel,
} from "./school/subscription-api";

import WorkspaceSidebar from "./workspace-sidebar";

type Step = { name: string; status: string; complete: boolean; href?: string };

// Keyed by school, so switching schools starts from fresh loading state.
function Workspace({ memberships }: { memberships: AuthMembership[] }) {
  const [selected, setSelected] = useState(memberships[0].tenant_id);
  const membership = memberships.find((item) => item.tenant_id === selected) ?? memberships[0];
  return (
    <WorkspaceBody
      key={membership.tenant_id}
      memberships={memberships}
      membership={membership}
      onSelect={setSelected}
    />
  );
}

function WorkspaceBody({
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
  const [students, setStudents] = useState<number | null>(null);
  const [studentsNote, setStudentsNote] = useState("Loading…");
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);

  useEffect(() => {
    let current = true;
    listStudents(authFetch, tenantId)
      .then((list) => {
        if (!current) return;
        setStudents(list.length);
        setStudentsNote(list.length === 1 ? "student enrolled" : "students enrolled");
      })
      .catch(() => current && setStudentsNote("Unavailable until the subscription is renewed"));
    getSubscription(authFetch, tenantId)
      .then((result) => current && setSubscription(result))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [authFetch, tenantId]);

  const hasAccess = subscription?.has_access ?? false;
  const steps: Step[] = [
    { name: "School created", status: "Done", complete: true },
    {
      name: "Subscription active",
      status: subscription === null ? "Checking" : hasAccess ? "Active" : "Needs renewal",
      complete: hasAccess,
      href: "#subscription",
    },
    {
      name: "Enrol your first student",
      status: students === null ? "—" : students > 0 ? `${students} enrolled` : "Not started",
      complete: (students ?? 0) > 0,
      href: "/school",
    },
    { name: "Invite staff", status: "Use the form below", complete: false, href: "#staff" },
    { name: "Invite parents and students", status: "From each student record", complete: false, href: "/school" },
  ];
  const doneCount = steps.filter((step) => step.complete).length;
  const next =
    !hasAccess && subscription !== null
      ? { title: "Renew your subscription", text: "Student records are locked until it is renewed. The steps are in the subscription section.", href: "#subscription", label: "See how to renew" }
      : students === 0
        ? { title: "Enrol your first student", text: "Add students, then invite their parents and the students themselves from each record.", href: "/school", label: "Enrol students" }
        : { title: "Invite your team", text: "Add school administrators, accountants and teachers so they can sign in.", href: "#staff", label: "Invite staff" };

  return (
    <main className="workspace">
      <WorkspaceSidebar
        memberships={memberships}
        membership={membership}
        onSelect={onSelect}
        active="overview"
      />

      <section className="main-panel" id="overview">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Overview</div>
          <div className="topbar-actions"><AuthControls /></div>
        </header>
        <div className="content">
          <section className="page-heading">
            <div>
              <p className="eyebrow">SCHOOL WORKSPACE</p>
              <h1>{membership.tenant_name}</h1>
              <p className="heading-copy">Set up your school, invite your team and keep an eye on your subscription.</p>
            </div>
            <div className="date-stamp">
              <span>{new Date().getDate()}</span>
              <small>{new Date().toLocaleDateString("en-GB", { month: "short" }).toUpperCase()}<br />{new Date().getFullYear()}</small>
            </div>
          </section>

          <section className="stats-row" aria-label="School summary">
            <article className="stat-block"><span className="stat-label">STUDENTS</span><strong>{students ?? "—"}</strong><span className="stat-note">{studentsNote}</span></article>
            <article className="stat-block">
              <span className="stat-label">PLAN</span>
              <strong>{subscription?.plan?.name ?? "—"}</strong>
              <span className="stat-note">
                {subscription?.plan?.billing_interval ? intervalLabel(subscription.plan.billing_interval) : subscription ? statusLabel(subscription.status) : "Loading…"}
              </span>
            </article>
            <article className="stat-block">
              <span className="stat-label">{subscription?.status === "trialing" ? "TRIAL ENDS" : "EXPIRES"}</span>
              <strong>{subscription ? formatDate(subscription.expires_at) : "—"}</strong>
              <span className="stat-note">{subscription ? statusLabel(subscription.status) : "Loading…"}</span>
            </article>
          </section>

          <section className="lower-grid">
            <div className="foundations-panel">
              <div className="section-heading">
                <div><p className="eyebrow">SETUP</p><h2>Get your school ready</h2></div>
                <span className="count-badge">{String(doneCount).padStart(2, "0")} / {String(steps.length).padStart(2, "0")}</span>
              </div>
              <div className="foundation-list">
                {steps.map((step, index) => (
                  <div className="foundation-row" key={step.name}>
                    <span className="row-number">0{index + 1}</span>
                    <span className="foundation-name">
                      {step.href ? (step.href.startsWith("#") ? <a href={step.href}>{step.name}</a> : <Link href={step.href}>{step.name}</Link>) : step.name}
                    </span>
                    <span className={`state-pill ${step.complete ? "ready" : "pending"}`}><span className="state-dot" />{step.status}</span>
                  </div>
                ))}
              </div>
            </div>

            <aside className="next-panel">
              <p className="eyebrow">NEXT UP</p>
              <div className="next-icon" aria-hidden="true">{String(doneCount + 1).padStart(2, "0")}</div>
              <h2>{next.title}</h2>
              <p>{next.text}</p>
              {next.href.startsWith("#") ? (
                <a className="primary-action" href={next.href}>{next.label} <span aria-hidden="true">→</span></a>
              ) : (
                <Link className="primary-action" href={next.href}>{next.label} <span aria-hidden="true">→</span></Link>
              )}
            </aside>
          </section>

          <div className="workspace-sections">
            <div id="staff"><StaffInvitePanel key={tenantId} tenantId={tenantId} authFetch={authFetch} /></div>
            <div id="subscription"><SubscriptionPanel key={tenantId} tenantId={tenantId} authFetch={authFetch} /></div>
          </div>

          <footer className="page-footer"><span>NLSchool · School operations</span></footer>
        </div>
      </section>
    </main>
  );
}

// Owners and school administrators get the setup workspace; everyone else
// goes straight to their role's records view.
export default function Home() {
  const { user } = useAuth();
  if (!user) return null;
  const managed = user.memberships.filter((item) => MANAGE_ROLES.includes(item.role));
  if (managed.length === 0) return <SchoolDashboard />;
  return <Workspace memberships={managed} />;
}

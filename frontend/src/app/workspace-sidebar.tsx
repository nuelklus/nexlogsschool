"use client";

import Link from "next/link";

import type { AuthMembership } from "./auth-provider";

export type SidebarPage = "overview" | "students" | "finance" | "staff" | "subscription";

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");

const FINANCE_ROLES = ["owner", "school_admin", "accountant"];

const LINKS: { page: SidebarPage; label: string; href: string }[] = [
  { page: "overview", label: "Overview", href: "/" },
  { page: "students", label: "Students", href: "/dashboard-add-student" },
  { page: "finance", label: "Finance", href: "/finance" },
  { page: "staff", label: "Staff", href: "/#staff" },
  { page: "subscription", label: "Subscription", href: "/#subscription" },
];

export default function WorkspaceSidebar({
  memberships,
  membership,
  onSelect,
  active,
}: {
  memberships: AuthMembership[];
  membership: AuthMembership;
  onSelect: (tenantId: string) => void;
  active: SidebarPage;
}) {
  return (
    <aside className="sidebar">
      <Link className="brand" href="/" aria-label="NLSchool overview">
        <span className="brand-mark">N</span>
        <span>NLSchool</span>
      </Link>
      <div className="workspace-label">WORKSPACE</div>
      {memberships.length > 1 ? (
        <label className="school-switch">
          School
          <select value={membership.tenant_id} onChange={(event) => onSelect(event.target.value)}>
            {memberships.map((item) => (
              <option key={item.tenant_id} value={item.tenant_id}>{item.tenant_name}</option>
            ))}
          </select>
        </label>
      ) : (
        <div className="school-switcher">
          <span className="school-avatar">{initials(membership.tenant_name)}</span>
          <span>{membership.tenant_name}</span>
        </div>
      )}
      <nav className="navigation" aria-label="Main navigation">
        {LINKS.filter((link) => link.page !== "finance" || FINANCE_ROLES.includes(membership.role)).map((link, index) => (
          <Link
            key={link.page}
            className={`nav-link${link.page === active ? " active" : ""}`}
            href={link.href}
            aria-current={link.page === active ? "page" : undefined}
          >
            <span>0{index + 1}</span>
            {link.label}
          </Link>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <span className="connection-dot" />
        {membership.role.replaceAll("_", " ")}
      </div>
    </aside>
  );
}

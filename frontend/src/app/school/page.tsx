import type { Metadata } from "next";

import SchoolDashboard from "./school-dashboard";

export const metadata: Metadata = { title: "My school · NLSchool" };

export default function SchoolPage() {
  return <SchoolDashboard />;
}

import type { Metadata } from "next";

import AuthGate from "../auth-gate";
import FinanceDashboardPage from "./dashboard-page";

export const metadata: Metadata = { title: "Finance · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <FinanceDashboardPage />
    </AuthGate>
  );
}

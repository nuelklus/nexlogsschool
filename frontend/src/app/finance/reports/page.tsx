import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import FinanceReportsPage from "../reports-page";

export const metadata: Metadata = { title: "Finance reports · NLSchool" };

export default function Page() {
  return <AuthGate><FinanceReportsPage /></AuthGate>;
}

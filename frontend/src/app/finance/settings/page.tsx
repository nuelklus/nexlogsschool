import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import FinanceSettingsPage from "../finance-settings-page";

export const metadata: Metadata = { title: "Finance settings · NLSchool" };

export default function Page() {
  return <AuthGate><FinanceSettingsPage /></AuthGate>;
}

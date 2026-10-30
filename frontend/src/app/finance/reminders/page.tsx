import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import FinanceRemindersPage from "../reminders-page";

export const metadata: Metadata = { title: "Fee reminders · NLSchool" };

export default function Page() {
  return <AuthGate><FinanceRemindersPage /></AuthGate>;
}

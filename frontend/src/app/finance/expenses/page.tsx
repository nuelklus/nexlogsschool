import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import { ExpensesPage } from "../accounting-pages";

export const metadata: Metadata = { title: "Expenses · NLSchool" };

export default function Page() {
  return <AuthGate><ExpensesPage /></AuthGate>;
}

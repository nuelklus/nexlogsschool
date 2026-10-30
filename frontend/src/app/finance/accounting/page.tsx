import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import { AccountingPage } from "../accounting-pages";

export const metadata: Metadata = { title: "Accounting · NLSchool" };

export default function Page() {
  return <AuthGate><AccountingPage /></AuthGate>;
}

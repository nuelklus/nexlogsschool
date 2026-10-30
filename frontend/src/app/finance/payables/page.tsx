import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import { PayablesPage } from "../accounting-pages";

export const metadata: Metadata = { title: "Accounts payable · NLSchool" };

export default function Page() {
  return <AuthGate><PayablesPage /></AuthGate>;
}

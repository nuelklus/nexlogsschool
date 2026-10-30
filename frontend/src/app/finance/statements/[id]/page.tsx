import type { Metadata } from "next";

import AuthGate from "../../../auth-gate";
import StatementPage from "../../statement-page";

export const metadata: Metadata = { title: "Statement · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <StatementPage />
    </AuthGate>
  );
}

import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import FeesPage from "../fees-page";

export const metadata: Metadata = { title: "Fees · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <FeesPage />
    </AuthGate>
  );
}

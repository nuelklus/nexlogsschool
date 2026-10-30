import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import PaymentsPage from "../payments-page";

export const metadata: Metadata = { title: "Payments · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <PaymentsPage />
    </AuthGate>
  );
}

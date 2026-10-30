import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import BillingPage from "../billing-page";

export const metadata: Metadata = { title: "Billing · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <BillingPage />
    </AuthGate>
  );
}

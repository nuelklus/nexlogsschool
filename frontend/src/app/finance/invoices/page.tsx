import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import InvoicesPage from "../invoices-page";

export const metadata: Metadata = { title: "Invoices · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <InvoicesPage />
    </AuthGate>
  );
}

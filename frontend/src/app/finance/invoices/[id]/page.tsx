import type { Metadata } from "next";

import AuthGate from "../../../auth-gate";
import InvoicePage from "../../invoice-page";

export const metadata: Metadata = { title: "Invoice · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <InvoicePage />
    </AuthGate>
  );
}

import type { Metadata } from "next";

import AuthGate from "../../../auth-gate";
import ReceiptPage from "../../receipt-page";

export const metadata: Metadata = { title: "Receipt · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <ReceiptPage />
    </AuthGate>
  );
}

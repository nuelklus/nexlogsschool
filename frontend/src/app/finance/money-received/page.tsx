import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import MoneyReceivedPage from "./money-received-page";

export const metadata: Metadata = { title: "Money received · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <MoneyReceivedPage />
    </AuthGate>
  );
}

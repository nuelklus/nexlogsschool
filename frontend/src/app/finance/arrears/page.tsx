import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import ArrearsPage from "../arrears-page";

export const metadata: Metadata = { title: "Arrears · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <ArrearsPage />
    </AuthGate>
  );
}

import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import OtherIncomePage from "./income-page";

export const metadata: Metadata = { title: "Other income · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <OtherIncomePage />
    </AuthGate>
  );
}

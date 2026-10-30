import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import AdvancesPage from "../advances-page";

export const metadata: Metadata = { title: "Advances · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <AdvancesPage />
    </AuthGate>
  );
}

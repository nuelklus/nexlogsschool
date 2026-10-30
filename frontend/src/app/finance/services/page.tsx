import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import ServicesPage from "../services-page";

export const metadata: Metadata = { title: "Services · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <ServicesPage />
    </AuthGate>
  );
}

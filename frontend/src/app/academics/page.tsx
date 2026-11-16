import type { Metadata } from "next";

import AuthGate from "../auth-gate";
import AcademicsPage from "./academics-page";

export const metadata: Metadata = { title: "Academic Management · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <AcademicsPage />
    </AuthGate>
  );
}

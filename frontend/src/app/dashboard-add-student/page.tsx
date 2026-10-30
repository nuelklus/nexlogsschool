import type { Metadata } from "next";

import AddStudentPage from "../add-student-page";
import AuthGate from "../auth-gate";

export const metadata: Metadata = { title: "Students · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <AddStudentPage />
    </AuthGate>
  );
}

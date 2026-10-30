import type { Metadata } from "next";

import AuthGate from "./auth-gate";
import WorkspaceHome from "./workspace-home";

export const metadata: Metadata = { title: "NLSchool" };

export default function Home() {
  return (
    <AuthGate>
      <WorkspaceHome />
    </AuthGate>
  );
}

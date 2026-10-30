import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import { SuppliersPage } from "../accounting-pages";

export const metadata: Metadata = { title: "Suppliers · NLSchool" };

export default function Page() {
  return <AuthGate><SuppliersPage /></AuthGate>;
}

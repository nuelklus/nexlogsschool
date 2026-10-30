import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import { SupplierBillsPage } from "../accounting-pages";

export const metadata: Metadata = { title: "Supplier bills · NLSchool" };

export default function Page() {
  return <AuthGate><SupplierBillsPage /></AuthGate>;
}

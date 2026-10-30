import type { Metadata } from "next";

import AuthGate from "../../auth-gate";
import TransactionsPage from "../transactions-page";

export const metadata: Metadata = { title: "Transactions · NLSchool" };

export default function Page() {
  return (
    <AuthGate>
      <TransactionsPage />
    </AuthGate>
  );
}

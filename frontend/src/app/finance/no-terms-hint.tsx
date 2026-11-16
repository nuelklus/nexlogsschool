import Link from "next/link";

import type { FinanceContext } from "./finance-shell";
import { useTermState } from "./use-term-options";

// Explains an empty term list: either no terms were set up yet, or all are closed.
export function NoTermsHint({ ctx, yearId }: { ctx: FinanceContext; yearId: string }) {
  const { terms, loaded, hasAny } = useTermState(ctx, yearId);
  if (!loaded || !yearId || terms.length > 0) return null;
  return (
    <p className="fin-note" role="status">
      {hasAny
        ? "All terms for this academic year are closed, so nothing can be billed."
        : <>No terms have been set up for this academic year. A school administrator can add them under <Link href="/#calendar">Overview → Academic years &amp; terms</Link>.</>}
    </p>
  );
}

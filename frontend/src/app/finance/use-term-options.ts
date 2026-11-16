import { useEffect, useState } from "react";
import { listAcademicRecords, type AcademicTerm } from "../academics/academics-api";
import type { FinanceContext } from "./finance-shell";

// Term names come from Academic Management for the chosen year; completed terms are hidden. Academic Management is the single source of terms.
export function useTermOptions(ctx: FinanceContext, yearId: string): string[] {
  return useTermState(ctx, yearId).terms;
}

export function useTermState(ctx: FinanceContext, yearId: string): { terms: string[]; loaded: boolean; hasAny: boolean } {
  const [all, setAll] = useState<AcademicTerm[]>([]);
  const [loaded, setLoaded] = useState(false);
  const { authFetch, tenantId } = ctx;

  useEffect(() => {
    let current = true;
    listAcademicRecords(authFetch, tenantId, "terms")
      .then((terms) => {
        if (!current) return;
        setAll(terms);
        setLoaded(true);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [authFetch, tenantId]);

  const forYear = all.filter((term) => term.academic_year === yearId && !term.is_completed).sort((a, b) => a.start_date.localeCompare(b.start_date));
  return { terms: forYear.map((term) => term.name), loaded, hasAny: all.some((term) => term.academic_year === yearId) };
}

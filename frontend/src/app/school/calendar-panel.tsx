"use client";

import { useCallback, useEffect, useState } from "react";

import { AcademicForm, RecordList } from "../academics/academics-page";
import {
  AcademicTerm,
  AcademicYear,
  createAcademicRecord,
  listAcademicRecords,
  updateTerm,
} from "../academics/academics-api";
import type { AuthFetch } from "./student-api";

// The school calendar: the one place academic years and terms are set up.
// Finance and Academic Management both read from here.
export function SchoolCalendarPanel({ tenantId, authFetch }: { tenantId: string; authFetch: AuthFetch }) {
  const [years, setYears] = useState<AcademicYear[]>([]);
  const [terms, setTerms] = useState<AcademicTerm[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [loadedYears, loadedTerms] = await Promise.all([
        listAcademicRecords(authFetch, tenantId, "years"),
        listAcademicRecords(authFetch, tenantId, "terms"),
      ]);
      setYears(loadedYears);
      setTerms(loadedTerms);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The school calendar could not be loaded.");
    }
  }, [authFetch, tenantId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const yearName = (id: string) => years.find((year) => year.id === id)?.name ?? "Year";
  const yearOptions = years.map((item) => ({ value: item.id, label: item.name }));
  const openTerms = terms.filter((item) => !item.is_completed);

  return (
    <section className="academic-card" id="calendar">
      <p className="eyebrow">SCHOOL CALENDAR</p>
      <h2>Academic years &amp; terms</h2>
      <p className="academic-empty">Set these up once. Finance, billing and Academic Management all use these terms, and each school enters its own dates.</p>
      {error && <p className="setup-error" role="alert">{error}</p>}
      <div className="academic-columns">
        <div>
          <AcademicForm
            title="Add academic year"
            submitLabel="Add year"
            onSubmit={async (data) => { await createAcademicRecord(authFetch, tenantId, "years", data); await refresh(); }}
            fields={[
              { name: "name", label: "Year name (e.g. 2026/2027)" },
              { name: "start_date", label: "Start date", type: "date" },
              { name: "end_date", label: "End date", type: "date" },
              { name: "is_current", label: "Set as current academic year", type: "checkbox", required: false },
            ]}
          />
          <h2>Academic years</h2>
          <RecordList
            rows={years.map((item) => ({
              id: item.id,
              title: item.name,
              detail: `${item.start_date} – ${item.end_date}${item.is_current ? " · Current" : ""}`,
            }))}
            emptyText="No academic years have been added."
          />
        </div>
        <div>
          <AcademicForm
            title="Add term"
            submitLabel="Add term"
            onSubmit={async (data) => { await createAcademicRecord(authFetch, tenantId, "terms", data); await refresh(); }}
            fields={[
              { name: "academic_year", label: "Academic year", type: "select", options: yearOptions },
              { name: "name", label: "Term name (e.g. Term 1)" },
              { name: "start_date", label: "Start date", type: "date" },
              { name: "end_date", label: "End date", type: "date" },
              { name: "is_current", label: "Set as current term", type: "checkbox", required: false },
            ]}
          />
          {openTerms.length > 0 && (
            <AcademicForm
              title="Change term name or dates"
              submitLabel="Update term"
              onSubmit={async (data) => {
                const { term, ...changes } = data as Record<string, string>;
                const filled = Object.fromEntries(Object.entries(changes).filter(([, value]) => value));
                if (Object.keys(filled).length === 0) throw new Error("Enter a new name or date to change.");
                await updateTerm(authFetch, tenantId, term, filled);
                await refresh();
              }}
              fields={[
                {
                  name: "term", label: "Term", type: "select",
                  options: openTerms.map((item) => ({ value: item.id, label: `${yearName(item.academic_year)} · ${item.name}` })),
                },
                { name: "name", label: "New name (optional)", required: false },
                { name: "start_date", label: "New start date (optional)", type: "date", required: false },
                { name: "end_date", label: "New end date (optional)", type: "date", required: false },
              ]}
            />
          )}
          <h2>Terms</h2>
          <RecordList
            rows={terms.map((item) => ({
              id: item.id,
              title: item.name,
              detail: `${yearName(item.academic_year)} · ${item.start_date} – ${item.end_date}${item.is_current ? " · Current" : ""}${item.is_completed ? " · Completed" : ""}`,
            }))}
            emptyText="No terms yet. Add your first term above."
          />
        </div>
      </div>
    </section>
  );
}

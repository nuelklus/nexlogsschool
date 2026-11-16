"use client";

import { useEffect, useState } from "react";

import { financeRequest, formatMoney } from "./finance-api";
import type { FinanceContext } from "./finance-shell";

export type StudentMatch = {
  id: string;
  name: string;
  admission_number: string;
  grade_level: string;
  amount_owed: string;
  advance_credit: string;
};

// Searches on demand so the whole school is never loaded into a dropdown.
export default function StudentPicker({
  ctx,
  value,
  onChange,
}: {
  ctx: FinanceContext;
  value: StudentMatch | null;
  onChange: (student: StudentMatch | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StudentMatch[]>([]);
  const [searching, setSearching] = useState(false);
  const { authFetch, tenantId } = ctx;
  const term = query.trim();

  useEffect(() => {
    if (term.length < 2) return;
    let current = true;
    const timer = window.setTimeout(() => {
      setSearching(true);
      financeRequest<StudentMatch[]>(authFetch, tenantId, `students/search/?q=${encodeURIComponent(term)}`)
        .then((rows) => current && setResults(rows))
        .catch(() => current && setResults([]))
        .finally(() => current && setSearching(false));
    }, 250);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [authFetch, tenantId, term]);

  if (value) {
    const owed = Number(value.amount_owed);
    return (
      <div className="enrol-wide student-picked" role="group" aria-label="Selected student">
        <p>
          <strong>{value.name}</strong> · {value.admission_number}
          {value.grade_level ? ` · ${value.grade_level}` : ""}{" "}
          <button type="button" className="link-button" onClick={() => onChange(null)}>Change</button>
        </p>
        <p className={owed > 0 ? "setup-error" : "enrol-success"}>
          {owed > 0 ? `Owes ${formatMoney(value.amount_owed)} on open invoices` : "Nothing owed on invoices"}
          {Number(value.advance_credit) > 0 ? ` · Advance held ${formatMoney(value.advance_credit)}` : ""}
        </p>
      </div>
    );
  }

  const shown = term.length >= 2 ? results : [];
  return (
    <div className="enrol-wide student-picker">
      <label>Student
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Type a name or admission number"
          autoComplete="off"
        />
      </label>
      {term.length >= 2 && !searching && shown.length === 0 && <p className="fin-note">No active student matches.</p>}
      {shown.length > 0 && (
        <ul className="student-picker-list" aria-label="Matching students">
          {shown.map((student) => (
            <li key={student.id}>
              <button type="button" onClick={() => { onChange(student); setQuery(""); setResults([]); }}>
                {student.name} · {student.admission_number}
                {student.grade_level ? ` · ${student.grade_level}` : ""}
                <small>Owes {formatMoney(student.amount_owed)}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

"use client";

import { FormEvent, useState } from "react";

import type { Student } from "../school/student-api";
import type { AcademicEnrollment, AcademicYear, SchoolClass, Subject, TeachingAssignment } from "./academics-api";

export default function ClassEnrollmentForm({
  students, classes, years, enrollments, subjects, assignments, onCreate,
}: {
  students: Student[];
  classes: SchoolClass[];
  years: AcademicYear[];
  enrollments: AcademicEnrollment[];
  subjects: Subject[];
  assignments: TeachingAssignment[];
  onCreate: (data: Record<string, string | boolean | string[]>) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [studentId, setStudentId] = useState("");
  const [classId, setClassId] = useState("");
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const matches = students.filter((student) =>
    `${student.first_name} ${student.last_name} ${student.admission_number}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  );
  const selectedStudent = students.find((student) => student.id === studentId);
  const visible = selectedStudent && !matches.some((student) => student.id === studentId)
    ? [selectedStudent, ...matches] : matches;
  const schoolClass = classes.find((item) => item.id === classId);
  const existing = enrollments.find((item) => item.student === studentId && item.school_class === classId);
  const otherActive = enrollments.find((item) => item.student === studentId && item.status === "active"
    && classes.some((itemClass) => itemClass.id === item.school_class
      && itemClass.academic_year === schoolClass?.academic_year));
  const assignedIds = new Set(assignments.filter((item) => item.school_class === classId).map((item) => item.subject));
  const classSubjects = subjects.filter((subject) => assignedIds.has(subject.id));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !studentId || !classId || !date || existing || otherActive) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await onCreate({ student: studentId, school_class: classId, enrolled_on: date });
      setMessage("Enrollment created. The student follows all subjects assigned to this class.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The enrollment could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form" aria-label="Manage student class enrollment" onSubmit={submit}>
      <h2>Manage student class enrollment</h2>
      <p className="fin-note">Admission already creates an enrollment. Students automatically follow all subjects assigned to their class.</p>
      <div className="academic-form-grid">
        <div className="academic-search-select">
          <label htmlFor="enrollment-student-search">Search student by name or admission number</label>
          <input id="enrollment-student-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder="Type a name or admission number" aria-describedby="enrollment-student-search-help" />
          <label htmlFor="enrollment-student">Choose student</label>
          <select id="enrollment-student" value={studentId} onChange={(event) => { setStudentId(event.target.value); setMessage(""); }} required>
            <option value="">Select a student</option>
            {visible.map((student) => <option key={student.id} value={student.id}>{student.first_name} {student.last_name} · {student.admission_number}</option>)}
          </select>
          <small id="enrollment-student-search-help" aria-live="polite">{matches.length ? `${matches.length} ${matches.length === 1 ? "student" : "students"} match.` : "No students match this search."}</small>
        </div>
        <label>Class
          <select value={classId} onChange={(event) => { setClassId(event.target.value); setMessage(""); }} required>
            <option value="">Select class</option>
            {classes.filter((item) => item.is_active !== false).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}{item.section ? ` · ${item.section}` : ""} ({years.find((year) => year.id === item.academic_year)?.name ?? "Year"})
              </option>
            ))}
          </select>
        </label>
        {!existing && !otherActive && <label>Enrollment date<input type="date" value={date} onChange={(event) => setDate(event.target.value)} required /></label>}
      </div>
      {schoolClass && <p className="fin-note">Class subjects: {classSubjects.length ? classSubjects.map((subject) => subject.name).join(", ") : "No subjects assigned yet. Add them under Subjects & teachers."}</p>}
      {existing && <p role="status" className="fin-note">
        {existing.status === "active" ? "This student is already enrolled in this class." : `This student has a ${existing.status} enrollment in this class. Review the existing record before making changes.`}
      </p>}
      {!existing && otherActive && <p role="status" className="fin-note">This student already has an active class enrollment in this academic year. Use the transfer workflow below for a current-year class change.</p>}
      {message && <p className="academic-success" role="status">{message}</p>}
      {error && <p className="setup-error" role="alert">{error}</p>}
      <button className="setup-submit" type="submit" disabled={busy || Boolean(existing || otherActive)}>
        {busy ? "Saving…" : "Create enrollment"}
      </button>
    </form>
  );
}

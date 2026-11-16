"use client";

import { FormEvent, useState } from "react";

import type { AuthFetch, Student } from "../school/student-api";
import {
  saveClassAttendance,
  type AcademicEnrollment, type AcademicYear, type AttendanceRecord, type SchoolClass,
  type TeachingAssignment,
} from "./academics-api";

const STATUSES = ["present", "absent", "late", "excused"] as const;
const label = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
const className = (item: SchoolClass) => `${item.name}${item.section ? ` · ${item.section}` : ""}`;
const studentName = (student: Student) => `${student.first_name} ${student.last_name} · ${student.admission_number}`;

type Props = {
  authFetch: AuthFetch;
  tenantId: string;
  userId: string;
  canManage: boolean;
  canTeach: boolean;
  years: AcademicYear[];
  classes: SchoolClass[];
  students: Student[];
  enrollments: AcademicEnrollment[];
  attendance: AttendanceRecord[];
  assignments: TeachingAssignment[];
  onSaved: () => Promise<void>;
};

export default function AttendancePanel({
  authFetch, tenantId, userId, canManage, canTeach, years, classes, students,
  enrollments, attendance, assignments, onSaved,
}: Props) {
  const [studentId, setStudentId] = useState("");
  const [yearId, setYearId] = useState("");
  const [classId, setClassId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [registerYear, setRegisterYear] = useState("");
  const [registerClass, setRegisterClass] = useState("");
  const [registerDate, setRegisterDate] = useState("");
  const [entries, setEntries] = useState<Record<string, { status: string; notes: string }>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const studentById = new Map(students.map((item) => [item.id, item]));
  const classById = new Map(classes.map((item) => [item.id, item]));
  const enrollmentById = new Map(enrollments.map((item) => [item.id, item]));
  const rows = attendance.flatMap((record) => {
    const enrollment = enrollmentById.get(record.enrollment);
    const schoolClass = enrollment && classById.get(enrollment.school_class);
    const student = enrollment && studentById.get(enrollment.student);
    if (!enrollment || !schoolClass) return [];
    if (studentId && enrollment.student !== studentId) return [];
    if (yearId && schoolClass.academic_year !== yearId) return [];
    if (classId && schoolClass.id !== classId) return [];
    if (from && record.date < from) return [];
    if (to && record.date > to) return [];
    if (statusFilter && record.status !== statusFilter) return [];
    return [{ record, schoolClass, student }];
  });
  const counts = Object.fromEntries(STATUSES.map((status) => [
    status, rows.filter((row) => row.record.status === status).length,
  ]));
  const writableClasses = classes.filter((item) =>
    canManage || (canTeach && assignments.some((assignment) =>
      assignment.school_class === item.id && assignment.teacher === userId)),
  );
  const roster = enrollments.filter((item) =>
    item.school_class === registerClass && registerDate &&
    (item.status === "active" || item.status === "transferred") &&
    item.enrolled_on <= registerDate && (!item.ended_on || item.ended_on >= registerDate),
  ).map((item) => ({
    enrollment: item,
    student: studentById.get(item.student),
    existing: attendance.find((record) => record.enrollment === item.id && record.date === registerDate),
  })).sort((a, b) => (a.student ? studentName(a.student) : "").localeCompare(b.student ? studentName(b.student) : ""));
  const pending = roster.filter((item) => !item.existing);

  function resetRegister() {
    setEntries({});
    setMessage("");
    setError("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !pending.length) return;
    if (pending.some((item) => !entries[item.enrollment.id]?.status)) {
      setError("Choose a status for every unrecorded student.");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const created = await saveClassAttendance(authFetch, tenantId, {
        school_class: registerClass,
        date: registerDate,
        entries: pending.map((item) => ({
          enrollment: item.enrollment.id,
          status: entries[item.enrollment.id].status,
          notes: entries[item.enrollment.id].notes,
        })),
      });
      setEntries({});
      setMessage(`Saved attendance for ${created} student(s). Existing records were not changed.`);
      await onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Attendance could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function reloadRegister() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await onSaved();
      setEntries({});
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The register could not be reloaded.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {(canManage || canTeach) && (
        <form className="academic-form" onSubmit={submit} aria-label="Class attendance register">
          <h2>Class attendance register</h2>
          <p className="fin-note">Choose a class and date. Existing attendance is shown but will not be changed.</p>
          <div className="academic-form-grid">
            <label>Register academic year
              <select value={registerYear} onChange={(event) => {
                setRegisterYear(event.target.value); setRegisterClass(""); resetRegister();
              }} required>
                <option value="">Select academic year</option>
                {years.map((year) => <option value={year.id} key={year.id}>{year.name}</option>)}
              </select>
            </label>
            <label>Register class
              <select value={registerClass} onChange={(event) => {
                setRegisterClass(event.target.value); resetRegister();
              }} required>
                <option value="">Select class</option>
                {writableClasses.filter((item) => item.academic_year === registerYear).map((item) => (
                  <option value={item.id} key={item.id}>{className(item)}</option>
                ))}
              </select>
            </label>
            <label>Register date
              <input type="date" value={registerDate} required onChange={(event) => {
                setRegisterDate(event.target.value); resetRegister();
              }} min={years.find((year) => year.id === registerYear)?.start_date}
              max={years.find((year) => year.id === registerYear)?.end_date} />
            </label>
          </div>
          {registerClass && registerDate && (
            roster.length === 0 ? <p>No enrollments cover this date in the selected class.</p> :
              <ul className="academic-records">
                {roster.map(({ enrollment, student, existing }) => (
                  <li key={enrollment.id}>
                    <strong>{student ? studentName(student) : "Student"}</strong>
                    {existing ? <span>Already recorded: {label(existing.status)}{existing.notes ? ` · ${existing.notes}` : ""}</span> : <>
                      <label>Status for {student ? studentName(student) : enrollment.id}
                        <select required value={entries[enrollment.id]?.status ?? ""} onChange={(event) => setEntries((current) => ({
                          ...current, [enrollment.id]: { status: event.target.value, notes: current[enrollment.id]?.notes ?? "" },
                        }))}>
                          <option value="">Select status</option>
                          {STATUSES.map((status) => <option key={status} value={status}>{label(status)}</option>)}
                        </select>
                      </label>
                      <label>Notes for {student ? studentName(student) : enrollment.id}
                        <input maxLength={240} value={entries[enrollment.id]?.notes ?? ""} onChange={(event) => setEntries((current) => ({
                          ...current, [enrollment.id]: { status: current[enrollment.id]?.status ?? "", notes: event.target.value },
                        }))} />
                      </label>
                    </>}
                  </li>
                ))}
              </ul>
          )}
          {error && <p className="setup-error" role="alert">{error}</p>}
          {message && <p className="academic-success" role="status">{message}</p>}
          {error && <button type="button" className="secondary-action" disabled={busy} onClick={reloadRegister}>Reload register</button>}
          <button type="submit" className="setup-submit" disabled={busy || !pending.length}>
            {busy ? "Saving…" : "Save class attendance"}
          </button>
        </form>
      )}
      <section aria-label="Attendance history">
        <h2>Attendance history</h2>
        <p className="fin-note">Select a student to see records across their class enrollments. Totals reflect the selected filters.</p>
        <div className="academic-form-grid">
          <label>Student
            <select value={studentId} onChange={(event) => setStudentId(event.target.value)}>
              <option value="">All students</option>
              {students.map((student) => <option key={student.id} value={student.id}>{studentName(student)}</option>)}
            </select>
          </label>
          <label>Academic year
            <select value={yearId} onChange={(event) => { setYearId(event.target.value); setClassId(""); }}>
              <option value="">All years</option>
              {years.map((year) => <option key={year.id} value={year.id}>{year.name}</option>)}
            </select>
          </label>
          <label>Class
            <select value={classId} onChange={(event) => setClassId(event.target.value)}>
              <option value="">All classes</option>
              {classes.filter((item) => !yearId || item.academic_year === yearId).map((item) => (
                <option key={item.id} value={item.id}>{className(item)} · {years.find((year) => year.id === item.academic_year)?.name ?? "Year"}</option>
              ))}
            </select>
          </label>
          <label>From date<input type="date" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>To date<input type="date" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} /></label>
          <label>Attendance status
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">All statuses</option>
              {STATUSES.map((status) => <option key={status} value={status}>{label(status)}</option>)}
            </select>
          </label>
        </div>
        <p>{STATUSES.map((status) => `${label(status)}: ${counts[status]}`).join(" · ")}</p>
        {rows.length === 0 ? <p>No attendance records match these filters.</p> : (
          <ul className="academic-records">
            {rows.map(({ record, schoolClass, student }) => (
              <li key={record.id}>
                <strong>{student ? studentName(student) : "Student"}</strong>
                <span>{record.date} · {className(schoolClass)} · {label(record.status)}{record.notes ? ` · ${record.notes}` : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

"use client";

import PromotionPanel from "./promotion-panel";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import AuthControls from "../auth-controls";
import { AuthMembership, useAuth } from "../auth-provider";
import WorkspaceSidebar from "../workspace-sidebar";
import TermClosurePanel from "./term-closure-panel";
import ClassLevelsPanel from "./class-levels-panel";
import AttendancePanel from "./attendance-panel";
import ExaminationPanel from "./examination-panel";
import ClassEnrollmentForm from "./class-enrollment-form";
import { Student, listStudents } from "../school/student-api";
import {
  AcademicCollection,
  AcademicCollections,
  AcademicEnrollment,
  AcademicYear,
  Assessment,
  AssessmentResult,
  SchoolClass,
  Subject,
  TimetableEntry,
  createAcademicRecord,
  transferClass,
  listAcademicRecords,
} from "./academics-api";

const READ_ROLES = ["owner", "school_admin", "accountant", "teacher", "headteacher"];
const MANAGE_ROLES = ["owner", "school_admin"];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Option = { value: string; label: string };
type Field = {
  name: string;
  label: string;
  type?: "text" | "date" | "time" | "number" | "select" | "multiselect" | "checkbox";
  options?: Option[];
  required?: boolean;
  min?: string;
  step?: string;
};

export function AcademicForm({
  title,
  fields,
  submitLabel,
  onSubmit,
}: {
  title: string;
  fields: Field[];
  submitLabel: string;
  onSubmit: (data: Record<string, string | boolean | string[]>) => Promise<void>;
}) {
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const formData = new FormData(form);
    const data: Record<string, string | boolean | string[]> = {};
    for (const field of fields) {
      if (field.type === "checkbox") {
        data[field.name] = formData.get(field.name) === "on";
      } else if (field.type === "multiselect") {
        data[field.name] = formData.getAll(field.name).map(String);
      } else {
        data[field.name] = String(formData.get(field.name) ?? "").trim();
      }
    }
    setBusy(true);
    setMessage("");
    try {
      await onSubmit(data);
      form.reset();
      setMessage("Saved successfully.");
      setFailed(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The request could not be completed.");
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="academic-form" onSubmit={submit}>
      <h2>{title}</h2>
      <div className="academic-form-grid">
        {fields.map((field) => (
          <label className={field.type === "multiselect" ? "academic-field-wide" : ""} key={field.name}>
            {field.type === "checkbox" ? (
              <span className="academic-checkbox">
                <input type="checkbox" name={field.name} />
                {field.label}
              </span>
            ) : field.type === "select" || field.type === "multiselect" ? (
              <>
                {field.label}
                <select
                  name={field.name}
                  required={field.required !== false}
                  multiple={field.type === "multiselect"}
                  size={field.type === "multiselect" ? Math.min(5, Math.max(2, field.options?.length ?? 2)) : undefined}
                >
                  {field.type === "select" && <option value="">Select {field.label.toLowerCase()}</option>}
                  {field.options?.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </>
            ) : (
              <>
                {field.label}
                <input
                  name={field.name}
                  type={field.type ?? "text"}
                  required={field.required !== false}
                  min={field.min}
                  step={field.step}
                />
              </>
            )}
          </label>
        ))}
      </div>
      {message && <p className={failed ? "setup-error" : "academic-success"} role={failed ? "alert" : "status"}>{message}</p>}
      <button className="setup-submit" type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
    </form>
  );
}

export function RecordList({ rows, emptyText }: { rows: { id: string; title: string; detail: string; action?: { label: string; run: () => void } }[]; emptyText: string }) {
  if (rows.length === 0) return <p className="academic-empty">{emptyText}</p>;
  return (
    <ul className="academic-records">
      {rows.map((row) => (
        <li key={row.id}><strong>{row.title}</strong><span>{row.detail}</span>{row.action && <button type="button" className="secondary-action" onClick={row.action.run}>{row.action.label}</button>}</li>
      ))}
    </ul>
  );
}

function AcademicWorkspace({
  membership,
  memberships,
  onSelect,
}: {
  membership: AuthMembership;
  memberships: AuthMembership[];
  onSelect: (tenantId: string) => void;
}) {
  const { authFetch, user } = useAuth();
  const tenantId = membership.tenant_id;
  const canManage = MANAGE_ROLES.includes(membership.role);
  const canTeach = membership.role === "teacher";
  const canViewClosure = ["owner", "school_admin", "headteacher", "accountant"].includes(membership.role);
  const [active, setActive] = useState("years");
  const [closureTerm, setClosureTerm] = useState("");
  const [records, setRecords] = useState<AcademicCollections | null>(null);
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [
      years,
      terms,
      classes,
      subjects,
      teachers,
      assignments,
      enrollments,
      attendance,
      timetable,
      assessments,
      results,
      studentList,
    ] = await Promise.all([
      listAcademicRecords(authFetch, tenantId, "years"),
      listAcademicRecords(authFetch, tenantId, "terms"),
      listAcademicRecords(authFetch, tenantId, "classes"),
      listAcademicRecords(authFetch, tenantId, "subjects"),
      listAcademicRecords(authFetch, tenantId, "teachers"),
      listAcademicRecords(authFetch, tenantId, "teacher-assignments"),
      listAcademicRecords(authFetch, tenantId, "enrollments"),
      listAcademicRecords(authFetch, tenantId, "attendance"),
      listAcademicRecords(authFetch, tenantId, "timetable"),
      listAcademicRecords(authFetch, tenantId, "assessments"),
      listAcademicRecords(authFetch, tenantId, "results"),
      listStudents(authFetch, tenantId),
    ]);
    return {
      academicRecords: {
        years, terms, classes, subjects, teachers, "teacher-assignments": assignments,
        enrollments, attendance, timetable, assessments, results,
      },
      students: studentList,
    };
  }, [authFetch, tenantId]);

  const refresh = useCallback(async () => {
    try {
      const loaded = await load();
      setRecords(loaded.academicRecords);
      setStudents(loaded.students);
      setError("");
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "Academic records could not be loaded.";
      setRecords(null);
      setError(`The change was saved, but the records could not be refreshed. Reload the page before making another change. ${detail}`);
    }
  }, [load]);

  useEffect(() => {
    let current = true;
    load()
      .then(({ academicRecords, students: studentList }) => {
        if (!current) return;
        setRecords(academicRecords);
        setStudents(studentList);
        setError("");
      })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "Academic records could not be loaded.");
      })
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [load]);

  const save = useCallback(async (
    collection: Exclude<AcademicCollection, "teachers">,
    data: Record<string, string | boolean | string[]>,
  ) => {
    await createAcademicRecord(authFetch, tenantId, collection, data);
    await refresh();
  }, [authFetch, refresh, tenantId]);

  const years = records?.years ?? [];
  const classes = records?.classes ?? [];
  const subjects = records?.subjects ?? [];
  const terms = records?.terms ?? [];
  const teachers = records?.teachers ?? [];
  const assignments = records?.["teacher-assignments"] ?? [];
  const enrollments = records?.enrollments ?? [];
  const assessments = records?.assessments ?? [];
  const schoolClassOptions = classes.filter((item) => item.is_active !== false).map((item) => ({
    value: item.id,
    label: `${item.name}${item.section ? ` · ${item.section}` : ""} (${years.find((year) => year.id === item.academic_year)?.name ?? "Year"})`,
  }));
  const subjectOptions = subjects.map((item) => ({ value: item.id, label: `${item.name} (${item.code})` }));
  const teacherOptions = teachers.map((item) => ({ value: item.id, label: item.name || item.email }));
  const enrollmentOptions = enrollments.map((item) => {
    const student = students.find((record) => record.id === item.student);
    return {
      value: item.id,
      label: `${student ? `${student.first_name} ${student.last_name}` : "Student"} · ${classes.find((cls) => cls.id === item.school_class)?.name ?? "Class"}`,
    };
  });
  const transferOptions = enrollments.filter((item) => item.status === "active" && classes.some(
    (cls) => cls.id === item.school_class && years.some((year) => year.id === cls.academic_year && year.is_current)
  )).map((item) => {
    const student = students.find((record) => record.id === item.student);
    const cls = classes.find((record) => record.id === item.school_class);
    return {
      value: item.id,
      label: `${student ? `${student.first_name} ${student.last_name} · ${student.admission_number}` : "Student"} · ${cls?.name ?? "Class"}${cls?.section ? ` ${cls.section}` : ""}`,
    };
  });
  const termOptions = terms.map((item) => ({
    value: item.id,
    label: `${item.name} · ${years.find((year) => year.id === item.academic_year)?.name ?? "Year"}`,
  }));
  const assessmentOptions = assessments.map((item) => ({
    value: item.id,
    label: `${item.name} · ${subjects.find((subject) => subject.id === item.subject)?.name ?? "Subject"}`,
  }));

  const tabs = [
    { id: "years", label: "Years & terms" },
    { id: "levels", label: "Class levels" },
    { id: "classes", label: "Classes" },
    { id: "teaching", label: "Subjects & teachers" },
    { id: "enrollment", label: "Enrollment" },
    { id: "attendance", label: "Attendance" },
    { id: "timetable", label: "Timetable" },
    { id: "exams", label: "Exams & results" },
    ...(canManage ? [{ id: "promotion", label: "Promotion" }] : []),
  ];

  const yearRows = (records?.years ?? []).map((item: AcademicYear) => ({
    id: item.id, title: item.name, detail: `${item.start_date} – ${item.end_date}${item.is_current ? " · Current" : ""}`,
  }));
  const termRows = terms.map((item) => ({
    id: item.id, title: item.name,
    detail: `${years.find((year) => year.id === item.academic_year)?.name ?? "Year"} · ${item.start_date} – ${item.end_date}${item.is_current ? " · Current" : ""}${item.is_completed ? " · Completed" : ""}`,
    action: canViewClosure ? { label: "Term closure", run: () => setClosureTerm(item.id) } : undefined,
  }));
  const classRows = classes.map((item: SchoolClass) => ({
    id: item.id, title: `${item.name}${item.section ? ` · ${item.section}` : ""}`,
    detail: `${years.find((year) => year.id === item.academic_year)?.name ?? "Year"}${item.room ? ` · Room ${item.room}` : ""}${item.is_active === false ? " · Archived" : ""}`,
  }));
  const subjectRows = subjects.map((item: Subject) => ({
    id: item.id, title: item.name, detail: item.code,
  }));
  const assignmentRows = assignments.map((item) => ({
    id: item.id,
    title: `${classes.find((cls) => cls.id === item.school_class)?.name ?? "Class"} · ${subjects.find((subject) => subject.id === item.subject)?.name ?? "Subject"}`,
    detail: teachers.find((teacher) => teacher.id === item.teacher)?.name ?? "Teacher",
  }));
  const enrollmentRows = enrollments.map((item: AcademicEnrollment) => {
    const student = students.find((record) => record.id === item.student);
    const cls = classes.find((record) => record.id === item.school_class);
    return {
      id: item.id,
      title: student ? `${student.first_name} ${student.last_name}` : "Student",
      detail: `${cls?.name ?? "Class"} · ${item.subjects.length} subject(s) · ${item.status}`,
    };
  });
  const timetableRows = (records?.timetable ?? []).map((item: TimetableEntry) => ({
    id: item.id,
    title: `${WEEKDAYS[item.weekday]} · ${item.start_time}–${item.end_time}`,
    detail: `${classes.find((cls) => cls.id === item.school_class)?.name ?? "Class"} · ${subjects.find((subject) => subject.id === item.subject)?.name ?? "Subject"} · ${teachers.find((teacher) => teacher.id === item.teacher)?.name ?? "Teacher"}`,
  }));
  const assessmentRows = assessments.map((item: Assessment) => ({
    id: item.id, title: item.name,
    detail: `${subjects.find((subject) => subject.id === item.subject)?.name ?? "Subject"} · ${classes.find((cls) => cls.id === item.school_class)?.name ?? "Class"} · ${item.assessment_date} · out of ${item.max_score}`,
  }));
  const resultRows = (records?.results ?? []).map((item: AssessmentResult) => {
    const assessment = assessments.find((record) => record.id === item.assessment);
    const enrollment = enrollments.find((record) => record.id === item.enrollment);
    const student = students.find((record) => record.id === enrollment?.student);
    return { id: item.id, title: student ? `${student.first_name} ${student.last_name}` : "Student", detail: `${assessment?.name ?? "Assessment"} · ${item.score}${item.remarks ? ` · ${item.remarks}` : ""}` };
  });

  return (
    <main className="workspace">
      <WorkspaceSidebar memberships={memberships} membership={membership} onSelect={onSelect} active="academics" />
      <section className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Academic Management</div>
          <div className="topbar-actions"><AuthControls /></div>
        </header>
        <div className="content">
          <section className="page-heading">
            <div>
              <p className="eyebrow">TEACHING & LEARNING</p>
              <h1>Academic Management</h1>
              <p className="heading-copy">Organize school years, classes, curriculum, attendance, timetables and student results.</p>
            </div>
            <Link className="primary-action" href="/school">Student records <span aria-hidden="true">→</span></Link>
          </section>
          <nav className="finance-tabs academic-tabs" aria-label="Academic sections">
            {tabs.map((tab) => (
              <button type="button" key={tab.id} className={active === tab.id ? "active" : ""} onClick={() => setActive(tab.id)}>
                {tab.label}
              </button>
            ))}
          </nav>
          {error && <p className="setup-error" role="alert">{error}</p>}
          {loading && <p role="status">Loading academic records…</p>}
          {!loading && records && (
            <div className="academic-content">
              {active === "levels" && <div>
                {canManage && <p className="fin-note">Manage class levels in <Link href="/#class-setup">Overview → Class levels &amp; classes</Link>.</p>}
                <ClassLevelsPanel key={tenantId} authFetch={authFetch} tenantId={tenantId} canManage={false} />
              </div>}
              {active === "years" && (
                <div className="academic-columns">
                  <section className="academic-card">
                    <h2>Academic years</h2>
                    <p className="academic-empty">Years and terms are set up on the <Link href="/#calendar">Overview</Link> page.</p>
                    <RecordList rows={yearRows} emptyText="No academic years have been added." />
                  </section>
                  {closureTerm ? (
                    <TermClosurePanel
                      authFetch={authFetch}
                      tenantId={tenantId}
                      termId={closureTerm}
                      onBack={() => setClosureTerm("")}
                      onChanged={refresh}
                      onOpenTab={(tab) => { setClosureTerm(""); setActive(tab); }}
                    />
                  ) : (
                    <section className="academic-card">
                      <h2>Terms</h2>
                      <p className="academic-empty">Terms are set on the Overview page. Closed terms no longer show in finance, and closing needs headteacher approval and then final authorization from the owner or an administrator.</p>
                      <RecordList rows={termRows} emptyText="No terms yet. A school administrator adds them on the Overview page." />
                    </section>
                  )}
                </div>
              )}
              {active === "classes" && (
                <section className="academic-card">
                  {canManage && <p className="fin-note">Create and manage classes in <Link href="/#class-setup">Overview → Class levels &amp; classes</Link>, then select Classes.</p>}
                  <h2>Classes</h2>
                  <RecordList rows={classRows} emptyText="No classes have been added." />
                </section>
              )}
              {active === "teaching" && (
                <div className="academic-columns">
                  <section className="academic-card">
                    {canManage && <AcademicForm title="Add subject" submitLabel="Add subject" onSubmit={(data) => save("subjects", data)} fields={[
                      { name: "name", label: "Subject name" },
                      { name: "code", label: "Subject code" },
                    ]} />}
                    <h2>Subjects</h2>
                    <RecordList rows={subjectRows} emptyText="No subjects have been added." />
                  </section>
                  <section className="academic-card">
                    {canManage && <AcademicForm title="Assign a teacher" submitLabel="Assign teacher" onSubmit={(data) => save("teacher-assignments", data)} fields={[
                      { name: "school_class", label: "Class", type: "select", options: schoolClassOptions },
                      { name: "subject", label: "Subject", type: "select", options: subjectOptions },
                      { name: "teacher", label: "Teacher", type: "select", options: teacherOptions },
                    ]} />}
                    <h2>Teacher assignments</h2>
                    <RecordList rows={assignmentRows} emptyText="No teachers have been assigned to classes yet." />
                  </section>
                </div>
              )}
              {active === "enrollment" && (
                <section className="academic-card">
                  {canManage && <ClassEnrollmentForm students={students} classes={classes} years={years} enrollments={enrollments}
                    subjects={subjects} assignments={assignments} onCreate={(data) => save("enrollments", data)} />}
                  {canManage && (
                    <>
                      <p className="fin-note">Transfers keep the original enrollment and its attendance and results. The student follows all subjects assigned to the destination class. Existing invoices do not change; finance still uses the student&apos;s current grade for future billing.</p>
                      <AcademicForm title="Transfer student to another class" submitLabel="Transfer student" onSubmit={async (data) => {
                        await transferClass(authFetch, tenantId, data);
                        await refresh();
                      }} fields={[
                        { name: "enrollment", label: "Current enrollment", type: "select", options: transferOptions },
                        { name: "target_class", label: "Destination class", type: "select", options: schoolClassOptions },
                        { name: "effective_date", label: "Effective date", type: "date" },
                        { name: "reason", label: "Reason for transfer" },
                      ]} />
                    </>
                  )}
                  <h2>Class enrollments</h2>
                  <RecordList rows={enrollmentRows} emptyText="No students are enrolled in academic classes yet." />
                </section>
              )}
              {active === "attendance" && (
                <section className="academic-card">
                  {(canManage || canTeach) && <AcademicForm title="Record attendance" submitLabel="Save attendance" onSubmit={(data) => save("attendance", data)} fields={[
                    { name: "enrollment", label: "Student and class", type: "select", options: enrollmentOptions },
                    { name: "date", label: "Date", type: "date" },
                    { name: "status", label: "Status", type: "select", options: ["present", "absent", "late", "excused"].map((item) => ({ value: item, label: item[0].toUpperCase() + item.slice(1) })) },
                    { name: "notes", label: "Notes", required: false },
                  ]} />}
                  <AttendancePanel
                    authFetch={authFetch} tenantId={tenantId} userId={user?.id ?? ""}
                    canManage={canManage} canTeach={canTeach} years={years} classes={classes}
                    students={students} enrollments={enrollments} attendance={records?.attendance ?? []}
                    assignments={assignments} onSaved={refresh}
                  />
                </section>
              )}
              {active === "timetable" && (
                <section className="academic-card">
                  {canManage && <AcademicForm title="Add timetable lesson" submitLabel="Add lesson" onSubmit={(data) => save("timetable", data)} fields={[
                    { name: "school_class", label: "Class", type: "select", options: schoolClassOptions },
                    { name: "subject", label: "Subject", type: "select", options: subjectOptions },
                    { name: "teacher", label: "Teacher", type: "select", options: teacherOptions },
                    { name: "weekday", label: "Day", type: "select", options: WEEKDAYS.map((day, index) => ({ value: String(index), label: day })) },
                    { name: "start_time", label: "Starts", type: "time" },
                    { name: "end_time", label: "Ends", type: "time" },
                    { name: "room", label: "Room", required: false },
                  ]} />}
                  <h2>Weekly timetable</h2>
                  <RecordList rows={timetableRows} emptyText="No timetable lessons have been added." />
                </section>
              )}
              {active === "promotion" && canManage && (
                <PromotionPanel authFetch={authFetch} tenantId={tenantId} years={records.years} classes={classes} onApplied={refresh} />
              )}
              {active === "exams" && (
                <>
                {membership.role !== "accountant" && <ExaminationPanel authFetch={authFetch} tenantId={tenantId} role={membership.role} terms={terms} classes={classes} subjects={subjects} />}
                <div className="academic-columns">
                  <section className="academic-card">
                    {canManage && <AcademicForm title="Create assessment" submitLabel="Create assessment" onSubmit={(data) => save("assessments", data)} fields={[
                      { name: "term", label: "Term", type: "select", options: termOptions },
                      { name: "school_class", label: "Class", type: "select", options: schoolClassOptions },
                      { name: "subject", label: "Subject", type: "select", options: subjectOptions },
                      { name: "name", label: "Assessment name" },
                      { name: "assessment_date", label: "Assessment date", type: "date" },
                      { name: "max_score", label: "Maximum score", type: "number", min: "0.01", step: "0.01" },
                    ]} />}
                    <h2>Assessments</h2>
                    <RecordList rows={assessmentRows} emptyText="No assessments have been created." />
                  </section>
                  <section className="academic-card">
                    {(canManage || canTeach) && <AcademicForm title="Record student result" submitLabel="Save result" onSubmit={(data) => save("results", data)} fields={[
                      { name: "assessment", label: "Assessment", type: "select", options: assessmentOptions },
                      { name: "enrollment", label: "Student and class", type: "select", options: enrollmentOptions },
                      { name: "score", label: "Score", type: "number", min: "0", step: "0.01" },
                      { name: "remarks", label: "Remarks", required: false },
                    ]} />}
                    <h2>Results</h2>
                    <RecordList rows={resultRows} emptyText="No student results have been recorded." />
                  </section>
                </div>
                </>
              )}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

export default function AcademicsPage() {
  const { user } = useAuth();
  const router = useRouter();
  const allowed = useMemo(() => user?.memberships.filter((item) => READ_ROLES.includes(item.role)) ?? [], [user]);
  const [selected, setSelected] = useState("");

  useEffect(() => {
    if (user && allowed.length === 0) router.replace("/");
  }, [allowed.length, router, user]);

  if (!user || allowed.length === 0) return null;
  const membership = allowed.find((item) => item.tenant_id === selected) ?? allowed[0];
  return (
    <AcademicWorkspace
      key={membership.tenant_id}
      membership={membership}
      memberships={allowed}
      onSelect={setSelected}
    />
  );
}

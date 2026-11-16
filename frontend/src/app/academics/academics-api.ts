import { AuthFetch, requestError } from "../school/student-api";

export type AcademicYear = {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: string;
  is_current: boolean;
};

export type AcademicTerm = {
  id: string;
  academic_year: string;
  name: string;
  start_date: string;
  end_date: string;
  is_current: boolean;
  is_completed: boolean;
};

export type SchoolClass = {
  id: string;
  academic_year: string;
  campus?: string | null;
  name: string;
  section: string;
  room: string;
  class_level?: string | null;
  class_teacher?: string | null;
  is_active?: boolean;
};

export type SchoolClassLevel = {
  id: string;
  catalogue_level: string | null;
  catalogue_name: string | null;
  display_name: string;
  sort_order: number;
  is_active: boolean;
  is_terminal: boolean;
};

export type Subject = { id: string; name: string; code: string };
export type Teacher = { id: string; name: string; email: string };
export type TeachingAssignment = {
  id: string;
  school_class: string;
  subject: string;
  teacher: string;
};
export type AcademicEnrollment = {
  id: string;
  student: string;
  school_class: string;
  subjects: string[];
  enrolled_on: string;
  ended_on?: string | null;
  status: "active" | "withdrawn" | "transferred";
};
export type AttendanceRecord = {
  id: string;
  enrollment: string;
  date: string;
  status: "present" | "absent" | "late" | "excused";
  notes: string;
};
export type TimetableEntry = {
  id: string;
  school_class: string;
  subject: string;
  teacher: string;
  weekday: number;
  start_time: string;
  end_time: string;
  room: string;
};
export type Assessment = {
  id: string;
  term: string;
  school_class: string;
  subject: string;
  name: string;
  assessment_date: string;
  max_score: string;
};
export type AssessmentResult = {
  id: string;
  assessment: string;
  enrollment: string;
  score: string;
  remarks: string;
};

export type AcademicCollections = {
  years: AcademicYear[];
  terms: AcademicTerm[];
  classes: SchoolClass[];
  subjects: Subject[];
  teachers: Teacher[];
  "teacher-assignments": TeachingAssignment[];
  enrollments: AcademicEnrollment[];
  attendance: AttendanceRecord[];
  timetable: TimetableEntry[];
  assessments: Assessment[];
  results: AssessmentResult[];
};

export type AcademicCollection = keyof AcademicCollections;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const isString = (value: unknown): value is string => typeof value === "string";

function isSchoolClass(value: unknown): value is SchoolClass {
  return isRecord(value) && isString(value.id) && isString(value.academic_year) && isString(value.name)
    && isString(value.section) && isString(value.room)
    && (value.campus === undefined || value.campus === null || isString(value.campus))
    && (value.class_level === undefined || value.class_level === null || isString(value.class_level))
    && (value.class_teacher === undefined || value.class_teacher === null || isString(value.class_teacher))
    && (value.is_active === undefined || typeof value.is_active === "boolean");
}

function isAcademicRecord(collection: AcademicCollection, value: unknown): boolean {
  if (!isRecord(value) || !isString(value.id)) return false;
  switch (collection) {
    case "years":
      return isString(value.name) && isString(value.start_date) && isString(value.end_date)
        && isString(value.status) && typeof value.is_current === "boolean";
    case "terms":
      return isString(value.academic_year) && isString(value.name)
        && isString(value.start_date) && isString(value.end_date) && typeof value.is_current === "boolean";
    case "classes":
      return isSchoolClass(value);
    case "subjects":
      return isString(value.name) && isString(value.code);
    case "teachers":
      return isString(value.name) && isString(value.email);
    case "teacher-assignments":
      return isString(value.school_class) && isString(value.subject) && isString(value.teacher);
    case "enrollments":
      return isString(value.student) && isString(value.school_class)
        && Array.isArray(value.subjects) && value.subjects.every(isString)
        && isString(value.enrolled_on)
        && (value.ended_on === undefined || value.ended_on === null || isString(value.ended_on))
        && (value.status === "active" || value.status === "withdrawn" || value.status === "transferred");
    case "attendance":
      return isString(value.enrollment) && isString(value.date)
        && isString(value.status) && isString(value.notes);
    case "timetable":
      return isString(value.school_class) && isString(value.subject) && isString(value.teacher)
        && typeof value.weekday === "number" && isString(value.start_time)
        && isString(value.end_time) && isString(value.room);
    case "assessments":
      return isString(value.term) && isString(value.school_class) && isString(value.subject)
        && isString(value.name) && isString(value.assessment_date) && isString(value.max_score);
    case "results":
      return isString(value.assessment) && isString(value.enrollment)
        && isString(value.score) && isString(value.remarks);
  }
}

async function decode(response: Response): Promise<unknown> {
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload;
}

export async function saveClassAttendance(
  authFetch: AuthFetch,
  tenantId: string,
  data: {
    school_class: string;
    date: string;
    entries: { enrollment: string; status: string; notes: string }[];
  },
): Promise<number> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/attendance/register/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }));
  if (!isRecord(payload) || typeof payload.created !== "number" || payload.created !== data.entries.length) {
    throw new Error("The server returned an unexpected attendance response. Reload before trying again.");
  }
  return payload.created;
}

function isSchoolClassLevel(value: unknown): value is SchoolClassLevel {
  return isRecord(value) && isString(value.id)
    && (value.catalogue_level === null || isString(value.catalogue_level))
    && (value.catalogue_name === null || isString(value.catalogue_name))
    && isString(value.display_name) && typeof value.sort_order === "number"
    && typeof value.is_active === "boolean" && typeof value.is_terminal === "boolean";
}

export async function listClassLevels(authFetch: AuthFetch, tenantId: string): Promise<SchoolClassLevel[]> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/class-levels/`));
  if (!Array.isArray(payload) || !payload.every(isSchoolClassLevel)) {
    throw new Error("The server returned an unexpected class-level response.");
  }
  return payload;
}

export async function assignClassLevel(
  authFetch: AuthFetch, tenantId: string, classId: string, levelId: string | null,
): Promise<SchoolClass> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/classes/${classId}/level/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ class_level: levelId }),
  }));
  if (!isSchoolClass(payload) || payload.class_level === undefined) {
    throw new Error("The server returned an unexpected class response.");
  }
  return payload;
}

export async function manageClass(
  authFetch: AuthFetch, tenantId: string, classId: string,
  data: { class_teacher?: string | null; is_active?: boolean; campus?: string | null },
): Promise<SchoolClass> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/classes/${classId}/management/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }));
  if (!isSchoolClass(payload) || payload.class_teacher === undefined || payload.is_active === undefined) {
    throw new Error("The server returned an unexpected class response.");
  }
  return payload;
}

export async function listAcademicRecords<K extends AcademicCollection>(
  authFetch: AuthFetch,
  tenantId: string,
  collection: K,
): Promise<AcademicCollections[K]> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/${collection}/`));
  if (!Array.isArray(payload) || !payload.every((item) => isAcademicRecord(collection, item))) {
    throw new Error("The server returned an unexpected academic response.");
  }
  return payload as AcademicCollections[K];
}

export async function createAcademicRecord(
  authFetch: AuthFetch,
  tenantId: string,
  collection: Exclude<AcademicCollection, "teachers">,
  data: Record<string, string | boolean | string[]>,
): Promise<unknown> {
  const response = await authFetch(`/tenants/${tenantId}/academics/${collection}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const payload = await decode(response);
  if (!isAcademicRecord(collection, payload)) {
    throw new Error("The server returned an unexpected academic response.");
  }
  return payload;
}

export async function transferClass(
  authFetch: AuthFetch,
  tenantId: string,
  data: Record<string, string | boolean | string[]>,
): Promise<void> {
  const payload = await decode(await authFetch(`/tenants/${tenantId}/academics/transfers/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }));
  if (!isRecord(payload) || !isAcademicRecord("enrollments", payload.previous)
    || !isAcademicRecord("enrollments", payload.enrollment)) {
    throw new Error("The server returned an unexpected transfer response.");
  }
}

export async function updateTerm(
  authFetch: AuthFetch,
  tenantId: string,
  termId: string,
  data: Record<string, string>,
): Promise<void> {
  const response = await authFetch(`/tenants/${tenantId}/academics/terms/${termId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  await decode(response);
}

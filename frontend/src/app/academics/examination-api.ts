import { AuthFetch, requestError } from "../school/student-api";

export type Component = { key: string; maximum: string; weight: string };
export type Band = { minimum: string; grade: string; description: string; passed: boolean };
export type Policy = {
  components: Component[]; bands: Band[]; decimal_places: number;
  rounding: "half_up" | "half_even" | "down"; allow_decimal_scores: boolean; exclude_not_applicable: boolean;
};
export type Configuration = {
  id: string; family: string; version: number; name: string; class_level: string | null;
  examination_type: string; configuration: Policy;
};
export type Examination = {
  id: string; name: string; term: string; school_class: string; subject: string; configuration: string;
  start_date: string; end_date: string; marks_deadline: string; publication_date: string; status: string;
  version: number; published_version: number;
};
export type MarkStatus = "scored" | "missing" | "absent" | "exempt" | "not_applicable";
export type ExamMark = { enrollment: string; component: string; status: MarkStatus; score: string | null; remarks: string };
export type ExamSession = { id: string; name: string; term: string; entries: (Examination & {
  class_name: string; section: string; subject_name: string;
})[]; counts: Record<string, number> };
export type ExamDetail = Examination & {
  policy: Policy; students: { enrollment: string; student: string; name: string; admission_number: string }[];
  marks: ExamMark[];
  calculations: { enrollment: string; score: string | null; grade: string | null; description: string | null;
    passed: boolean | null; error: string | null }[];
  publication_history: { version: number; published_at: string; result: { student_name: string; score: string; grade: string } }[];
};

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const str = (value: unknown): value is string => typeof value === "string";
const isPolicy = (value: unknown): value is Policy => record(value)
  && Array.isArray(value.components) && value.components.every((item: unknown) =>
    record(item) && str(item.key) && str(item.maximum) && str(item.weight))
  && Array.isArray(value.bands) && value.bands.every((item: unknown) =>
    record(item) && str(item.minimum) && str(item.grade) && str(item.description) && typeof item.passed === "boolean")
  && typeof value.decimal_places === "number" && ["half_up", "half_even", "down"].includes(String(value.rounding))
  && typeof value.allow_decimal_scores === "boolean" && typeof value.exclude_not_applicable === "boolean";
const isConfiguration = (value: unknown): value is Configuration => record(value)
  && str(value.id) && str(value.family) && typeof value.version === "number" && str(value.name)
  && (value.class_level === null || str(value.class_level)) && str(value.examination_type) && isPolicy(value.configuration);
const isExam = (value: unknown): value is Examination => record(value)
  && ["id", "name", "term", "school_class", "subject", "configuration", "start_date", "end_date",
    "marks_deadline", "publication_date", "status"].every((key) => str(value[key]))
  && typeof value.version === "number" && typeof value.published_version === "number";
const isDetail = (value: unknown): value is ExamDetail => record(value) && isPolicy(value.policy)
  && Array.isArray(value.students) && value.students.every((item: unknown) => record(item)
    && ["enrollment", "student", "name", "admission_number"].every((key) => str(item[key])))
  && Array.isArray(value.marks) && value.marks.every((item: unknown) => record(item)
    && str(item.enrollment) && str(item.component) && ["scored", "missing", "absent", "exempt", "not_applicable"].includes(String(item.status))
    && (item.score === null || str(item.score)) && str(item.remarks))
  && Array.isArray(value.calculations) && value.calculations.every((item: unknown) => record(item)
    && str(item.enrollment) && (item.score === null || str(item.score)) && (item.grade === null || str(item.grade))
    && (item.description === null || str(item.description)) && (item.error === null || str(item.error))
    && (item.passed === null || typeof item.passed === "boolean"))
  && Array.isArray(value.publication_history) && value.publication_history.every((item: unknown) => record(item)
    && typeof item.version === "number" && str(item.published_at) && record(item.result)
    && str(item.result.student_name) && str(item.result.score) && str(item.result.grade)) && isExam(value);

function validationMessage(value: unknown, path = ""): string | null {
  if (typeof value === "string") return path ? `${path}: ${value}` : value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const message = validationMessage(item, path);
      if (message) return message;
    }
  } else if (record(value)) {
    for (const [key, item] of Object.entries(value)) {
      const message = validationMessage(item, key === "non_field_errors" ? path : [path, key.replaceAll("_", " ")].filter(Boolean).join(" · "));
      if (message) return message;
    }
  }
  return null;
}

async function request(fetch: AuthFetch, path: string, data?: unknown): Promise<unknown> {
  const response = await fetch(path, data === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status !== 402) {
      const message = validationMessage(payload);
      if (message) throw new Error(message);
    }
    throw requestError(response, payload);
  }
  return payload;
}

export async function listExamConfigs(fetch: AuthFetch, tenant: string): Promise<Configuration[]> {
  const data = await request(fetch, `/tenants/${tenant}/academics/grading-configurations/`);
  if (!Array.isArray(data) || !data.every(isConfiguration)) throw new Error("Unexpected grading configuration response.");
  return data;
}
export async function createExamConfig(fetch: AuthFetch, tenant: string, data: unknown): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/grading-configurations/`, data);
  if (!isConfiguration(result)) throw new Error("Unexpected grading configuration response. Reload before retrying.");
}
export async function reviseExamConfig(fetch: AuthFetch, tenant: string, id: string, data: unknown): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/grading-configurations/${id}/revisions/`, data);
  if (!isConfiguration(result)) throw new Error("Unexpected grading configuration response. Reload before retrying.");
}
export async function listExams(fetch: AuthFetch, tenant: string): Promise<Examination[]> {
  const data = await request(fetch, `/tenants/${tenant}/academics/examinations/`);
  if (!Array.isArray(data) || !data.every(isExam)) throw new Error("Unexpected examination response.");
  return data;
}
export async function createExam(fetch: AuthFetch, tenant: string, data: unknown): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/examinations/`, data);
  if (!isExam(result)) throw new Error("Unexpected examination response. Reload before retrying.");
}
export async function createExamSession(fetch: AuthFetch, tenant: string, data: unknown): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/examination-sessions/`, data);
  if (!record(result) || !str(result.id) || !Array.isArray(result.entries) || !result.entries.every(isExam)) {
    throw new Error("Unexpected examination session response. Reload before retrying.");
  }
}
export async function listExamSessions(fetch: AuthFetch, tenant: string): Promise<ExamSession[]> {
  const data = await request(fetch, `/tenants/${tenant}/academics/examination-sessions/`);
  if (!Array.isArray(data) || !data.every((item: unknown) => record(item) && str(item.id) && str(item.name)
    && str(item.term) && record(item.counts) && ["draft", "submitted", "approved", "published"].every((key) => typeof (item.counts as Record<string, unknown>)[key] === "number")
    && Array.isArray(item.entries) && item.entries.every((entry: unknown) => record(entry)
      && str(entry.class_name) && str(entry.section) && str(entry.subject_name) && isExam(entry)))) {
    throw new Error("Unexpected examination sessions response.");
  }
  return data;
}
export async function getExam(fetch: AuthFetch, tenant: string, id: string): Promise<ExamDetail> {
  const data = await request(fetch, `/tenants/${tenant}/academics/examinations/${id}/`);
  if (!isDetail(data)) throw new Error("Unexpected examination detail response.");
  return data;
}
export async function saveMarks(fetch: AuthFetch, tenant: string, id: string, entries: ExamMark[]): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/examinations/${id}/`, { entries });
  if (!record(result) || result.saved !== entries.length) throw new Error("Unexpected marks response. Reload before retrying.");
}
export async function examAction(fetch: AuthFetch, tenant: string, id: string, action: string, reason = ""): Promise<void> {
  const result = await request(fetch, `/tenants/${tenant}/academics/examinations/${id}/${action}/`, { reason });
  if (!isExam(result)) throw new Error("Unexpected examination response. Reload before retrying.");
}

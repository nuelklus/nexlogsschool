export type Student = {
  id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  date_of_birth: string | null;
  gender: string;
  grade_level: string;
  status: "active" | "withdrawn" | "graduated";
  enrolled_on: string;
  portal_account: boolean;
};

export type Child = Student & { relationship: string; is_primary: boolean };

export type NewStudent = {
  first_name: string;
  last_name: string;
  date_of_birth?: string;
  gender?: string;
  grade_level?: string;
};

export type AuthFetch = (path: string, init?: RequestInit) => Promise<Response>;

export const MANAGE_ROLES = ["owner", "school_admin"];
export const READ_ROLES = [...MANAGE_ROLES, "accountant", "teacher"];

const STATUSES = ["active", "withdrawn", "graduated"];

export function getErrorMessage(payload: unknown, fallback = "The request could not be completed."): string {
  if (typeof payload === "object" && payload !== null) {
    const detail = "detail" in payload ? payload.detail : undefined;
    if (typeof detail === "string") return detail;
    for (const [field, value] of Object.entries(payload)) {
      const text = Array.isArray(value) ? value[0] : value;
      if (typeof text === "string") return `${field.replaceAll("_", " ")}: ${text}`;
    }
  }
  return fallback;
}

export class SubscriptionExpiredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SubscriptionExpiredError";
  }
}

// The API answers 402 when the school's subscription has lapsed.
export function requestError(response: Response, payload: unknown): Error {
  const message = getErrorMessage(payload);
  return response.status === 402 ? new SubscriptionExpiredError(message) : new Error(message);
}

export function isStudent(value: unknown): value is Student {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.admission_number === "string" &&
    typeof v.first_name === "string" &&
    typeof v.last_name === "string" &&
    (v.date_of_birth === null || typeof v.date_of_birth === "string") &&
    typeof v.gender === "string" &&
    typeof v.grade_level === "string" &&
    typeof v.status === "string" &&
    STATUSES.includes(v.status) &&
    typeof v.enrolled_on === "string" &&
    typeof v.portal_account === "boolean"
  );
}

export function isChild(value: unknown): value is Child {
  if (!isStudent(value)) return false;
  const v = value as unknown as Record<string, unknown>;
  return typeof v.relationship === "string" && typeof v.is_primary === "boolean";
}

async function parse<T>(
  response: Response,
  guard: (value: unknown) => value is T,
): Promise<T[]> {
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  if (!Array.isArray(payload) || !payload.every(guard)) {
    throw new Error("The server returned an unexpected response.");
  }
  return payload;
}

export async function listStudents(authFetch: AuthFetch, tenantId: string): Promise<Student[]> {
  return parse(await authFetch(`/tenants/${tenantId}/students/`), isStudent);
}

export async function listChildren(authFetch: AuthFetch, tenantId: string): Promise<Child[]> {
  return parse(await authFetch(`/tenants/${tenantId}/me/children/`), isChild);
}

export async function getOwnStudent(authFetch: AuthFetch, tenantId: string): Promise<Student> {
  const response = await authFetch(`/tenants/${tenantId}/me/student/`);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  if (!isStudent(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

async function postJson(authFetch: AuthFetch, path: string, body: unknown): Promise<unknown> {
  const response = await authFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload;
}

export async function enrollStudent(
  authFetch: AuthFetch,
  tenantId: string,
  input: NewStudent,
): Promise<Student> {
  // The backend rejects empty optional dates, so blank fields are omitted.
  const body = Object.fromEntries(
    Object.entries(input).filter(([, value]) => value !== undefined && value !== ""),
  );
  const payload = await postJson(authFetch, `/tenants/${tenantId}/students/`, body);
  if (!isStudent(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

export type InviteResult = { status: "invited" | "linked"; email: string };

function isInviteResult(value: unknown): value is InviteResult {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (v.status === "invited" || v.status === "linked") && typeof v.email === "string";
}

export async function inviteGuardian(
  authFetch: AuthFetch,
  tenantId: string,
  studentId: string,
  input: { email: string; relationship: string; is_primary: boolean },
): Promise<InviteResult> {
  const payload = await postJson(
    authFetch,
    `/tenants/${tenantId}/students/${studentId}/guardians/`,
    input,
  );
  if (!isInviteResult(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

export async function invitePortal(
  authFetch: AuthFetch,
  tenantId: string,
  studentId: string,
  email: string,
): Promise<InviteResult> {
  const payload = await postJson(
    authFetch,
    `/tenants/${tenantId}/students/${studentId}/portal-invitation/`,
    { email },
  );
  if (!isInviteResult(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

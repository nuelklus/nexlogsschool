import { AuthFetch, requestError } from "./student-api";

export const STAFF_ROLES = [
  { value: "school_admin", label: "School administrator" },
  { value: "accountant", label: "Accountant" },
  { value: "teacher", label: "Teacher" },
] as const;

export type StaffInvitation = { id: string; email: string; role: string; expires_at: string };

function isStaffInvitation(value: unknown): value is StaffInvitation {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.email === "string" &&
    typeof v.role === "string" &&
    typeof v.expires_at === "string"
  );
}

export async function inviteStaff(
  authFetch: AuthFetch,
  tenantId: string,
  input: { email: string; role: string },
): Promise<StaffInvitation> {
  const response = await authFetch(`/tenants/${tenantId}/invitations/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  if (!isStaffInvitation(payload)) throw new Error("The server returned an unexpected response.");
  return payload;
}

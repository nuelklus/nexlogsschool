import { requestError, type AuthFetch } from "../school/student-api";

export type ReportCard = {
  id: string; enrollment: string; version: number; status: "reviewed" | "published" | "withdrawn";
  snapshot: {
    student: string; admission_number: string; term: string; year: string; class: string; section: string;
    campus: string | null; overall_average: string; overall_method: string;
    class_teacher_remark: string; headteacher_remark: string; attendance: Record<string, number>;
    results: { publication_id: string; version: number;
      result: { subject: string; examination: string; score: string; grade: string; description: string } }[];
  };
};
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
function isReport(value: unknown): value is ReportCard {
  return record(value) && typeof value.id === "string" && typeof value.enrollment === "string"
    && typeof value.version === "number" && ["reviewed", "published", "withdrawn"].includes(String(value.status))
    && record(value.snapshot) && ["student", "admission_number", "term", "year", "class", "section",
      "overall_average", "overall_method", "class_teacher_remark", "headteacher_remark"].every((key) =>
      typeof (value.snapshot as Record<string, unknown>)[key] === "string")
    && (value.snapshot.campus === null || typeof value.snapshot.campus === "string")
    && record(value.snapshot.attendance) && Object.values(value.snapshot.attendance).every((item) => typeof item === "number")
    && Array.isArray(value.snapshot.results) && value.snapshot.results.every((item: unknown) =>
      record(item) && typeof item.publication_id === "string" && typeof item.version === "number" && record(item.result)
      && ["subject", "examination", "score", "grade", "description"].every((key) =>
        typeof (item.result as Record<string, unknown>)[key] === "string"));
}

export async function reportRequest(fetch: AuthFetch, path: string, data?: unknown): Promise<unknown> {
  const response = await fetch(path, data === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw requestError(response, payload);
  return payload;
}

export async function listReportCards(fetch: AuthFetch, tenant: string): Promise<ReportCard[]> {
  const result = await reportRequest(fetch, `/tenants/${tenant}/academics/report-cards/`);
  if (!Array.isArray(result) || !result.every(isReport)) throw new Error("Unexpected report card response.");
  return result;
}
export async function changeReport(fetch: AuthFetch, tenant: string, suffix: string, data: unknown): Promise<void> {
  const result = await reportRequest(fetch, `/tenants/${tenant}/academics/report-cards/${suffix}`, data);
  if (!isReport(result)) throw new Error("Unexpected report card response. Reload before retrying.");
}
export async function downloadReport(fetch: AuthFetch, tenant: string, id: string, version: number): Promise<void> {
  const response = await fetch(`/tenants/${tenant}/academics/report-cards/${id}/pdf/`);
  if (!response.ok) throw requestError(response, await response.json().catch(() => null));
  if (!response.headers.get("content-type")?.includes("application/pdf")) throw new Error("Unexpected PDF response.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = `report-card-v${version}.pdf`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadClassReports(fetch: AuthFetch, tenant: string, term: string, schoolClass: string, rosterDate: string): Promise<void> {
  const response = await fetch(`/tenants/${tenant}/academics/report-cards/class-export/`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ term, school_class: schoolClass, roster_date: rosterDate }),
  });
  if (!response.ok) throw requestError(response, await response.json().catch(() => null));
  if (!response.headers.get("content-type")?.includes("application/zip")) throw new Error("Unexpected class report export response.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = "class-report-cards.zip"; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

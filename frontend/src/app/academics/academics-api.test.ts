import { describe, expect, it, vi } from "vitest";

import { AuthFetch } from "../school/student-api";
import { createAcademicRecord, listAcademicRecords, transferClass } from "./academics-api";

const reply = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

const year = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "2026/2027",
  start_date: "2026-09-01",
  end_date: "2027-07-31",
  status: "active",
  is_current: true,
};

describe("academic requests", () => {
  it("loads validated records from the selected school's academic API", async () => {
    const authFetch = vi.fn<AuthFetch>(() => reply([year]));

    await expect(listAcademicRecords(authFetch, "tenant-1", "years")).resolves.toEqual([year]);
    expect(authFetch).toHaveBeenCalledWith("/tenants/tenant-1/academics/years/");
  });

  it("rejects malformed academic response shapes", async () => {
    const authFetch = vi.fn<AuthFetch>(() => reply([{ id: 5 }]));

    await expect(listAcademicRecords(authFetch, "tenant-1", "years")).rejects.toThrow(
      "unexpected academic response",
    );
  });

  it("posts academic records and surfaces API validation errors", async () => {
    const authFetch = vi.fn<AuthFetch>(() => reply(year, 201));
    const created = await createAcademicRecord(authFetch, "tenant-1", "years", {
      name: year.name,
      start_date: year.start_date,
      end_date: year.end_date,
      is_current: true,
    });

    expect(created).toEqual(year);
    expect(authFetch).toHaveBeenCalledWith(
      "/tenants/tenant-1/academics/years/",
      expect.objectContaining({ method: "POST" }),
    );

    const failedFetch = vi.fn<AuthFetch>(() => reply({ detail: "Term dates overlap." }, 400));
    await expect(createAcademicRecord(failedFetch, "tenant-1", "terms", {})).rejects.toThrow(
      "Term dates overlap.",
    );
  });

  it("submits transfers with subjects and validates the response", async () => {
    const previous = {
      id: "old", student: "student", school_class: "class-a", subjects: [],
      enrolled_on: "2026-01-01", ended_on: "2026-03-31", status: "transferred",
    };
    const enrollment = {
      id: "new", student: "student", school_class: "class-b", subjects: ["math"],
      enrolled_on: "2026-04-01", ended_on: null, status: "active",
    };
    const data = {
      enrollment: "old", target_class: "class-b", effective_date: "2026-04-01",
      reason: "Class change", subjects: ["math"],
    };
    const authFetch = vi.fn<AuthFetch>(() => reply({ previous, enrollment }, 201));
    await expect(transferClass(authFetch, "tenant-1", data)).resolves.toBeUndefined();
    expect(authFetch).toHaveBeenCalledWith(
      "/tenants/tenant-1/academics/transfers/",
      expect.objectContaining({ method: "POST", body: JSON.stringify(data) }),
    );

    const invalid = vi.fn<AuthFetch>(() => reply({ enrollment }, 201));
    await expect(transferClass(invalid, "tenant-1", data)).rejects.toThrow("unexpected transfer response");
    const invalidStatus = vi.fn<AuthFetch>(() => reply({
      previous: { ...previous, status: "unknown" }, enrollment,
    }, 201));
    await expect(transferClass(invalidStatus, "tenant-1", data)).rejects.toThrow("unexpected transfer response");
    const failed = vi.fn<AuthFetch>(() => reply({ detail: "Transfer rejected." }, 400));
    await expect(transferClass(failed, "tenant-1", data)).rejects.toThrow("Transfer rejected.");
  });
});

import { describe, expect, it, vi } from "vitest";

import {
  AuthFetch,
  enrollStudent,
  getErrorMessage,
  inviteGuardian,
  isChild,
  isStudent,
  listChildren,
  listStudents,
} from "./student-api";

const student = {
  id: "11111111-1111-1111-1111-111111111111",
  admission_number: "A-001",
  first_name: "Esi",
  last_name: "Boateng",
  date_of_birth: null,
  gender: "",
  grade_level: "",
  status: "active",
  enrolled_on: "2026-10-25",
  portal_account: false,
};

const reply = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe("response guards", () => {
  it("accepts the backend student shape and rejects malformed data", () => {
    expect(isStudent(student)).toBe(true);
    expect(isStudent({ ...student, status: "unknown" })).toBe(false);
    expect(isStudent({ ...student, portal_account: "no" })).toBe(false);
    expect(isStudent(null)).toBe(false);
  });

  it("requires relationship details for children", () => {
    expect(isChild({ ...student, relationship: "Mother", is_primary: true })).toBe(true);
    expect(isChild(student)).toBe(false);
  });
});

describe("getErrorMessage", () => {
  it("prefers detail, then the first field error", () => {
    expect(getErrorMessage({ detail: "Nope" })).toBe("Nope");
    expect(getErrorMessage({ admission_number: ["Taken"] })).toBe("admission number: Taken");
    expect(getErrorMessage(null, "Fallback")).toBe("Fallback");
  });
});

describe("student requests", () => {
  it("lists students for the selected tenant", async () => {
    const authFetch = vi.fn(() => reply([student]));
    await expect(listStudents(authFetch, "t1")).resolves.toEqual([student]);
    expect(authFetch).toHaveBeenCalledWith("/tenants/t1/students/");
  });

  it("surfaces backend errors such as tenant isolation 404s", async () => {
    const authFetch = vi.fn(() => reply({ detail: "Not found." }, 404));
    await expect(listStudents(authFetch, "other")).rejects.toThrow("Not found.");
  });

  it("rejects unexpected response shapes", async () => {
    const authFetch = vi.fn(() => reply([{ id: 1 }]));
    await expect(listChildren(authFetch, "t1")).rejects.toThrow("unexpected response");
  });

  it("omits blank optional fields when enrolling", async () => {
    const authFetch = vi.fn<AuthFetch>(() => reply(student, 201));
    await enrollStudent(authFetch, "t1", {
      first_name: "Esi",
      last_name: "Boateng",
      date_of_birth: "",
      gender: "",
    });
    const [path, init] = authFetch.mock.calls[0];
    expect(path).toBe("/tenants/t1/students/");
    expect(JSON.parse(String(init?.body))).toEqual({
      first_name: "Esi",
      last_name: "Boateng",
    });
  });

  it("posts guardian invitations to the student's guardian route", async () => {
    const authFetch = vi.fn<AuthFetch>(() =>
      reply({ status: "invited", email: "yaa@example.com" }, 201),
    );
    const result = await inviteGuardian(authFetch, "t1", "s1", {
      email: "yaa@example.com",
      relationship: "Mother",
      is_primary: true,
    });
    expect(result.status).toBe("invited");
    expect(authFetch.mock.calls[0][0]).toBe("/tenants/t1/students/s1/guardians/");
  });
});

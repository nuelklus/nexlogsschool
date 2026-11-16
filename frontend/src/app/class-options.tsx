"use client";

import { useEffect, useState } from "react";

import { AuthFetch, requestError } from "./school/student-api";

export function useClassOptions(authFetch: AuthFetch, tenantId: string, purpose: "admission" | "finance") {
  const [state, setState] = useState<{ tenant: string; purpose: string; fetch: AuthFetch | null; options: string[] | null; error: string }>({
    tenant: "", purpose: "", fetch: null, options: null, error: "",
  });
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const response = await authFetch(`/tenants/${tenantId}/academics/class-options/?purpose=${purpose}`);
        const payload: unknown = await response.json();
        if (!response.ok) throw requestError(response, payload);
        if (!Array.isArray(payload) || !payload.every((item) => typeof item === "string")) {
          throw new Error("The server returned an unexpected class options response.");
        }
        if (active) setState({ tenant: tenantId, purpose, fetch: authFetch, options: payload, error: "" });
      } catch (error) {
        if (active) setState({ tenant: tenantId, purpose, fetch: authFetch, options: null, error: error instanceof Error ? error.message : "Class options could not be loaded." });
      }
    }
    void load();
    return () => { active = false; };
  }, [authFetch, tenantId, purpose]);
  return state.tenant === tenantId && state.purpose === purpose && state.fetch === authFetch
    ? state : { options: null, error: "" };
}

export function ClassOptionsStatus({ options, error }: { options: string[] | null; error: string }) {
  if (error) return <p className="setup-error" role="alert">{error}</p>;
  if (!options) return <p className="fin-note" role="status">Loading classes…</p>;
  if (!options.length) return <p className="fin-note">No class options are available for this account and school.</p>;
  return null;
}

export function ClassOptions({ options }: { options: string[] | null }) {
  return options?.map((name) => <option key={name} value={name}>{name}</option>) ?? null;
}

export function CampusOptions({ campuses }: { campuses: { id: string; code: string; name: string; status: string }[] | null }) {
  return campuses?.filter((campus) => campus.status === "active").map((campus) => (
    <option key={campus.id} value={campus.id}>{campus.code} {campus.name}</option>
  )) ?? null;
}

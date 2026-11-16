"use client";

import { FormEvent, useState } from "react";
import type { AuthFetch } from "../school/student-api";
import { createExamConfig, reviseExamConfig, type Band, type Component, type Configuration } from "./examination-api";

type Props = {
  authFetch: AuthFetch; tenantId: string; canManage: boolean;
  configs: Configuration[]; onSaved: () => Promise<void>;
};

function ConfigurationForm({ initial, busy, onSubmit, onCancel }: {
  initial: Configuration | null; busy: boolean;
  onSubmit: (data: unknown) => Promise<void>; onCancel?: () => void;
}) {
  const [components, setComponents] = useState<Component[]>(
    () => initial?.configuration.components.map((item) => ({ ...item })) ?? [{ key: "", maximum: "", weight: "" }],
  );
  const [bands, setBands] = useState<Band[]>(
    () => initial?.configuration.bands.map((item) => ({ ...item })) ?? [{ minimum: "0", grade: "", description: "", passed: false }],
  );
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void onSubmit({
      name: data.get("name"), examination_type: data.get("type"), class_level: initial?.class_level ?? null,
      configuration: {
        components, bands, decimal_places: Number(data.get("places")), rounding: data.get("rounding"),
        allow_decimal_scores: data.has("allow_decimals"), exclude_not_applicable: data.has("exclude_na"),
      },
    });
  }
  return <form className="academic-form" onSubmit={submit}>
    <label>Name <input name="name" required maxLength={120} defaultValue={initial?.name ?? ""} /></label>
    <label>Examination type <input name="type" required maxLength={80} defaultValue={initial?.examination_type ?? ""} /></label>
    <h3>Components (weights must total 100%)</h3>
    {components.map((item, index) => <fieldset key={index}>
      <legend>Component {index + 1}</legend>
      <label>Name <input required value={item.key} maxLength={80} onChange={(e) => setComponents(components.map((row, i) => i === index ? { ...row, key: e.target.value } : row))} /></label>
      <label>Maximum <input required type="number" min="0.01" step="0.01" value={item.maximum} onChange={(e) => setComponents(components.map((row, i) => i === index ? { ...row, maximum: e.target.value } : row))} /></label>
      <label>Weight (%) <input required type="number" min="0.01" max="100" step="0.01" value={item.weight} onChange={(e) => setComponents(components.map((row, i) => i === index ? { ...row, weight: e.target.value } : row))} /></label>
      {components.length > 1 && <button type="button" onClick={() => setComponents(components.filter((_, i) => i !== index))}>Remove component</button>}
    </fieldset>)}
    <button type="button" onClick={() => setComponents([...components, { key: "", maximum: "", weight: "" }])}>Add component</button>
    <h3>Grade bands (minimum score; include zero)</h3>
    {bands.map((item, index) => <fieldset key={index}>
      <legend>Grade band {index + 1}</legend>
      <label>Minimum <input required type="number" min="0" max="100" step="0.01" value={item.minimum} onChange={(e) => setBands(bands.map((row, i) => i === index ? { ...row, minimum: e.target.value } : row))} /></label>
      <label>Grade <input required maxLength={40} value={item.grade} onChange={(e) => setBands(bands.map((row, i) => i === index ? { ...row, grade: e.target.value } : row))} /></label>
      <label>Description <input maxLength={240} value={item.description} onChange={(e) => setBands(bands.map((row, i) => i === index ? { ...row, description: e.target.value } : row))} /></label>
      <label><input type="checkbox" checked={item.passed} onChange={(e) => setBands(bands.map((row, i) => i === index ? { ...row, passed: e.target.checked } : row))} /> Passing grade</label>
      {bands.length > 1 && <button type="button" onClick={() => setBands(bands.filter((_, i) => i !== index))}>Remove band</button>}
    </fieldset>)}
    <button type="button" onClick={() => setBands([...bands, { minimum: "", grade: "", description: "", passed: false }])}>Add grade band</button>
    <label>Decimal places <input name="places" type="number" min="0" max="6" defaultValue={initial?.configuration.decimal_places ?? 2} required /></label>
    <label>Rounding <select name="rounding" defaultValue={initial?.configuration.rounding ?? "half_up"}><option value="half_up">Half up</option><option value="half_even">Half even</option><option value="down">Down</option></select></label>
    <label><input name="allow_decimals" type="checkbox" defaultChecked={initial?.configuration.allow_decimal_scores ?? false} /> Allow decimal marks</label>
    <label><input name="exclude_na" type="checkbox" defaultChecked={initial?.configuration.exclude_not_applicable ?? false} /> Exclude not-applicable components and rescale</label>
    <button type="submit" disabled={busy}>{initial ? "Save new version" : "Save configuration"}</button>
    {onCancel && <button type="button" disabled={busy} onClick={onCancel}>Cancel editing</button>}
  </form>;
}

export default function GradingConfigurations({ authFetch, tenantId, canManage, configs, onSaved }: Props) {
  const [editing, setEditing] = useState<Configuration | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function save(data: unknown, initial: Configuration | null) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      if (initial) await reviseExamConfig(authFetch, tenantId, initial.id, data);
      else await createExamConfig(authFetch, tenantId, data);
      setEditing(null);
      setFormKey((key) => key + 1);
      try {
        await onSaved();
        setMessage(initial ? "New grading configuration version saved." : "Grading configuration saved.");
      } catch {
        setError("The configuration was saved, but the list could not be refreshed. Reload the page before continuing.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the grading configuration.");
    } finally { setBusy(false); }
  }

  return <section aria-label="Grading configurations">
    <h3>Saved grading configurations</h3>
    <p>Editing creates a new version. Existing examinations keep their original configuration.</p>
    {error && <p className="setup-error" role="alert">{error}</p>}
    {message && <p className="academic-success" role="status">{message}</p>}
    {configs.length === 0 && <p>No grading configurations have been saved.</p>}
    <ul className="academic-records">{configs.map((config) => {
      const latest = !configs.some((item) => item.family === config.family && item.version > config.version);
      return <li key={config.id}>
        <details>
          <summary>{config.name} · {config.examination_type} · v{config.version}{latest ? " · Latest" : " · Previous version"}</summary>
          <p>Components</p>
          <ul>{config.configuration.components.map((item) => <li key={item.key}>{item.key}: maximum {item.maximum}, weight {item.weight}%</li>)}</ul>
          <p>Grade bands</p>
          <ul>{config.configuration.bands.map((item) => <li key={item.minimum}>From {item.minimum}%: {item.grade}{item.description ? ` · ${item.description}` : ""} · {item.passed ? "Pass" : "Not passing"}</li>)}</ul>
          <p>Decimal places: {config.configuration.decimal_places} · Rounding: {config.configuration.rounding.replaceAll("_", " ")} · Decimal marks: {config.configuration.allow_decimal_scores ? "Allowed" : "Not allowed"} · Not applicable: {config.configuration.exclude_not_applicable ? "Excluded and rescaled" : "Blocks calculation"}</p>
          {config.class_level && <p>Class level ID: {config.class_level}</p>}
          {canManage && latest && <button type="button" disabled={busy || editing !== null} onClick={() => { setError(""); setMessage(""); setEditing(config); }}>Edit {config.name} v{config.version}</button>}
        </details>
      </li>;
    })}</ul>
    {canManage && editing && <section aria-label="Edit grading configuration">
      <h3>Edit {editing.name} v{editing.version}</h3>
      <p>Saving creates version {editing.version + 1}; the current version and existing exams remain unchanged.</p>
      <ConfigurationForm key={editing.id} initial={editing} busy={busy} onSubmit={(data) => save(data, editing)} onCancel={() => setEditing(null)} />
    </section>}
    {canManage && !editing && <details>
      <summary>Create grading configuration</summary>
      <ConfigurationForm key={formKey} initial={null} busy={busy} onSubmit={(data) => save(data, null)} />
    </details>}
  </section>;
}

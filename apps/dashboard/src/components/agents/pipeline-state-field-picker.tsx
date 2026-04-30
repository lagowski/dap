"use client";

import { useId, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import {
  PIPELINE_STATE_FIELDS,
  PIPELINE_STATE_GROUPS,
  isPipelineStateField,
  type PipelineStateField,
  type PipelineStateGroup,
} from "@/lib/pipeline-state-fields";

interface PipelineStateFieldPickerProps {
  /** Currently-selected field names. */
  value: readonly string[];
  /** Called with the next selection on every toggle. */
  onChange: (next: string[]) => void;
  /** Inline hint shown above the picker — explains the field's role. */
  description?: string;
  /** Disable interaction (read-only display in version history etc.). */
  disabled?: boolean;
}

/**
 * Multi-select for `PipelineState` field names, grouped by the same
 * categories the Pydantic model uses. Backs `Agent.input_schema` and
 * `Agent.output_schema` in AgentForm.
 *
 * Read-only behaviours kept simple:
 * - Search filters by field name + description. Empty groups disappear.
 * - Selected fields aren't pinned to the top — order in the source list
 *   is the canonical UI order so users build a mental map.
 * - No "select all" — encourages deliberate contracts.
 */
export function PipelineStateFieldPicker({
  value,
  onChange,
  description,
  disabled = false,
}: PipelineStateFieldPickerProps) {
  const [query, setQuery] = useState("");
  const inputId = useId();
  const selected = useMemo(() => new Set(value), [value]);

  const grouped = useMemo(() => groupAndFilter(query), [query]);
  // Selected field names that aren't in the static mirror — typically
  // means the backend has been updated with new PipelineState fields and
  // an existing agent uses one we don't know about yet. Surface them so
  // the user can still see + un-select them; they wouldn't show up in
  // the regular groups otherwise (and "Clear" is the wrong tool — it
  // wipes everything).
  const unknownSelected = useMemo(
    () =>
      value
        .filter((name) => !isPipelineStateField(name))
        .filter((name) => matchesQuery(name, query)),
    [value, query],
  );
  const visibleCount =
    grouped.reduce((n, g) => n + g.fields.length, 0) + unknownSelected.length;

  const toggle = (name: string) => {
    if (disabled) return;
    if (selected.has(name)) {
      onChange(value.filter((v) => v !== name));
    } else {
      onChange([...value, name]);
    }
  };

  return (
    <div className="space-y-2">
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}

      <Input
        id={inputId}
        type="search"
        placeholder="Search fields…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        disabled={disabled}
        className="text-sm"
        aria-label="Filter fields"
      />

      <div className="rounded-md border bg-card">
        <div className="flex items-center justify-between border-b px-3 py-2 text-xs text-muted-foreground">
          <span>
            {value.length} selected
            {value.length > 0 ? ` · ${value.join(", ")}` : ""}
          </span>
          {value.length > 0 ? (
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
              onClick={() => onChange([])}
              disabled={disabled}
            >
              Clear
            </button>
          ) : null}
        </div>

        <div className="max-h-72 overflow-y-auto">
          {visibleCount === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              No fields match &quot;{query}&quot;.
            </div>
          ) : (
            <>
              {unknownSelected.length > 0 ? (
                <div className="border-b last:border-b-0">
                  <div className="flex items-baseline justify-between bg-amber-50 px-3 py-1 text-xs font-medium uppercase tracking-wide text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                    <span>Unknown / not in current schema</span>
                    <span className="text-[10px] normal-case">
                      possibly a newer engine field — frontend may be out of date
                    </span>
                  </div>
                  <ul>
                    {unknownSelected.map((name) => (
                      <UnknownFieldRow
                        key={name}
                        name={name}
                        onRemove={() => toggle(name)}
                        disabled={disabled}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}
              {grouped.map((group) => (
                <div key={group.group} className="border-b last:border-b-0">
                  <div className="bg-muted/40 px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {group.group}
                  </div>
                  <ul>
                    {group.fields.map((field) => (
                      <FieldRow
                        key={field.name}
                        field={field}
                        checked={selected.has(field.name)}
                        onToggle={() => toggle(field.name)}
                        disabled={disabled}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

interface FieldRowProps {
  field: PipelineStateField;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
}

function FieldRow({ field, checked, onToggle, disabled }: FieldRowProps) {
  const id = useId();
  return (
    <li className="border-b last:border-b-0">
      <label
        htmlFor={id}
        className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-accent/30 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
      >
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          className="mt-1 h-4 w-4 rounded border-input"
        />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-sm">{field.name}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {field.type}
            </span>
            {field.required ? (
              <span className="text-[10px] font-medium uppercase text-amber-600 dark:text-amber-400">
                required
              </span>
            ) : null}
            {/* Concrete example value, inline so it doesn't grow the
                row height. The "e.g." prefix makes it parse as a
                hint rather than the actual current value. */}
            <span
              className="font-mono text-xs text-muted-foreground/70 truncate"
              title={`Example value: ${field.example}`}
            >
              e.g. {field.example}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{field.description}</p>
        </div>
      </label>
    </li>
  );
}

function UnknownFieldRow({
  name,
  onRemove,
  disabled,
}: {
  name: string;
  onRemove: () => void;
  disabled: boolean;
}) {
  const id = useId();
  return (
    <li className="border-b last:border-b-0">
      <label
        htmlFor={id}
        className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-accent/30 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
      >
        <input
          id={id}
          type="checkbox"
          checked
          onChange={onRemove}
          disabled={disabled}
          className="mt-1 h-4 w-4 rounded border-input"
        />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-sm">{name}</span>
            <span className="text-[10px] font-medium uppercase text-amber-700 dark:text-amber-400">
              unknown
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            Selected but not in this dashboard&apos;s field list. Untick to
            remove from the agent&apos;s schema.
          </p>
        </div>
      </label>
    </li>
  );
}

interface GroupedFields {
  group: PipelineStateGroup;
  fields: PipelineStateField[];
}

function matchesQuery(haystack: string, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return needle === "" || haystack.toLowerCase().includes(needle);
}

function groupAndFilter(query: string): GroupedFields[] {
  const needle = query.trim().toLowerCase();
  const matches = (field: PipelineStateField) =>
    needle === "" ||
    field.name.toLowerCase().includes(needle) ||
    field.description.toLowerCase().includes(needle) ||
    field.type.toLowerCase().includes(needle);

  return PIPELINE_STATE_GROUPS.map((group) => ({
    group,
    fields: PIPELINE_STATE_FIELDS.filter(
      (f) => f.group === group && matches(f),
    ),
  })).filter((g) => g.fields.length > 0);
}

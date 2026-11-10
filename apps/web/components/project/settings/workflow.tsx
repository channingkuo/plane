/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Checkbox } from "@makeplane/propel/components/checkbox";
import { Select, SelectTrigger, SelectContent, SelectList, SelectItem } from "@makeplane/propel/components/select";
import { useTranslation } from "@plane/i18n";
import type { TProjectWorkflow, TWorkflowBranch, TWorkflowField, TWorkflowState } from "@plane/types";
import { workflowService } from "@/services/workflow.service";

const stateKeys = [
  "task_initial_state_id",
  "intake_submission_state_id",
  "intake_acceptance_state_id",
  "requirement_completed_state_id",
  "requirement_reopen_state_id",
] as const;
const fields: TWorkflowField[] = ["assignees", "start_date", "target_date"];

function StateChoice({
  value,
  states,
  label,
  onChange,
}: {
  value: string | null;
  states: TWorkflowState[];
  label: string;
  onChange: (value: string) => void;
}) {
  return (
    <Select
      items={states.map((state) => ({ value: state.id, label: state.name }))}
      value={value}
      onValueChange={(next) => next && onChange(next)}
    >
      <SelectTrigger size="lg" aria-label={label} placeholder={label} />
      <SelectContent>
        <SelectList>
          {states.map((state) => (
            <SelectItem key={state.id} value={state.id} label={state.name} />
          ))}
        </SelectList>
      </SelectContent>
    </Select>
  );
}

export function ProjectWorkflowSettings({ workspaceSlug, projectId }: { workspaceSlug: string; projectId: string }) {
  const { t } = useTranslation();
  const label = (key: string) => t(`project_settings.workflow.${key}`);
  const [draft, setDraft] = useState<TProjectWorkflow | null>(null);
  const [branch, setBranch] = useState<TWorkflowBranch>("task");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const { data, error, isLoading, mutate } = useSWR(
    ["project-workflow", workspaceSlug, projectId],
    () => workflowService.configuration(workspaceSlug, projectId),
    { revalidateOnFocus: false }
  );
  const working = draft ?? data;
  if (isLoading) return <p role="status">{label("loading")}</p>;
  if (error || !working) return <p role="alert">{label("load_error")}</p>;
  const states = data?.states ?? [];
  const update = (patch: Partial<TProjectWorkflow>) => setDraft({ ...working, ...patch });
  const save = async (preview: boolean) => {
    setBusy(true);
    setMessage("");
    setConflict(false);
    try {
      if (preview) {
        await workflowService.validate(workspaceSlug, projectId, working);
        setMessage(label("valid"));
      } else {
        const result = await workflowService.save(workspaceSlug, projectId, working);
        await mutate({ ...result, states }, false);
        setDraft(null);
        setMessage(label("saved"));
      }
    } catch (failure) {
      const response = (failure as { response?: { status?: number; data?: { message?: string; details?: unknown } } })
        .response;
      setConflict(response?.status === 409);
      setMessage(response?.status === 409 ? label("conflict") : (response?.data?.message ?? label("save_error")));
    } finally {
      setBusy(false);
    }
  };
  const addRequiredPaths = () => {
    const transitions = working.transitions.map((row) => ({ ...row, roles: [...row.roles] }));
    const pairs = [
      [working.intake_acceptance_state_id, working.requirement_completed_state_id],
      [working.requirement_reopen_state_id, working.requirement_completed_state_id],
      [working.requirement_completed_state_id, working.requirement_reopen_state_id],
    ];
    for (const [source, target] of pairs) {
      if (!source || !target) continue;
      const existing = transitions.find(
        (rule) => rule.branch === "requirement" && rule.from_state_id === source && rule.to_state_id === target
      );
      if (existing) existing.roles = [...new Set([...existing.roles, 20])];
      else transitions.push({ branch: "requirement", from_state_id: source, to_state_id: target, roles: [20] });
    }
    update({ transitions });
  };
  return (
    <section className="flex flex-col gap-6" aria-label={label("heading")}>
      <p>{label("description")}</p>
      <Checkbox label={label("enabled")} checked={working.enabled} onCheckedChange={(enabled) => update({ enabled })} />
      <div className="grid gap-4 md:grid-cols-2">
        {stateKeys.map((key) => {
          const options = states.filter((state) =>
            key === "requirement_completed_state_id"
              ? state.group === "completed"
              : !["completed", "cancelled"].includes(state.group) &&
                (key === "intake_submission_state_id" || state.group !== "triage")
          );
          return (
            <div key={key} className="flex flex-col gap-2">
              <span>{label(key)}</span>
              <StateChoice
                value={working[key]}
                states={options}
                label={label(key)}
                onChange={(value) => update({ [key]: value })}
              />
            </div>
          );
        })}
      </div>
      <div className="rounded border border-subtle p-4 text-body-sm-regular">{label("system_paths")}</div>
      <div className="flex flex-wrap gap-2">
        {(["task", "requirement"] as const).map((kind) => (
          <Button
            size="lg"
            stretch="auto"
            key={kind}
            variant={branch === kind ? "primary" : "secondary"}
            onClick={() => setBranch(kind)}
            label={label(kind)}
          />
        ))}
        <Button
          size="lg"
          stretch="auto"
          variant="secondary"
          onClick={addRequiredPaths}
          label={label("required_paths")}
        />
      </div>
      <div className="flex flex-col gap-4">
        <h3>{label("transitions")}</h3>
        {working.transitions.map((rule, index) =>
          rule.branch !== branch ? null : (
            // eslint-disable-next-line react/no-array-index-key -- Controlled draft rows have editable state IDs.
            <div key={index} className="flex flex-wrap items-center gap-3 rounded border border-subtle p-3">
              <StateChoice
                states={states}
                value={rule.from_state_id}
                label={label("from")}
                onChange={(value) =>
                  update({
                    transitions: working.transitions.map((row, i) =>
                      i === index ? { ...row, from_state_id: value } : row
                    ),
                  })
                }
              />
              <span aria-hidden="true">→</span>
              <StateChoice
                states={states}
                value={rule.to_state_id}
                label={label("to")}
                onChange={(value) =>
                  update({
                    transitions: working.transitions.map((row, i) =>
                      i === index ? { ...row, to_state_id: value } : row
                    ),
                  })
                }
              />
              {[20, 15, 5].map((role) => (
                <Checkbox
                  key={role}
                  label={label(`role_${role}`)}
                  checked={rule.roles.includes(role)}
                  onCheckedChange={(checked) =>
                    update({
                      transitions: working.transitions.map((row, i) =>
                        i === index
                          ? {
                              ...row,
                              roles: checked ? [...row.roles, role] : row.roles.filter((value) => value !== role),
                            }
                          : row
                      ),
                    })
                  }
                />
              ))}
              <Button
                size="lg"
                stretch="auto"
                variant="secondary"
                onClick={() => update({ transitions: working.transitions.filter((_, i) => i !== index) })}
                label={label("remove")}
              />
            </div>
          )
        )}
        <Button
          size="lg"
          stretch="auto"
          variant="secondary"
          disabled={!states.length}
          onClick={() =>
            update({
              transitions: [
                ...working.transitions,
                { branch, from_state_id: states[0].id, to_state_id: states[1]?.id ?? states[0].id, roles: [20] },
              ],
            })
          }
          label={label("add_transition")}
        />
      </div>
      <div className="flex flex-col gap-4">
        <h3>{label("requirements")}</h3>
        {states.map((state) => {
          const current =
            working.requirements.find((row) => row.branch === branch && row.state_id === state.id)?.fields ?? [];
          return (
            <fieldset key={state.id} className="flex flex-wrap items-center gap-4">
              <legend>{state.name}</legend>
              {fields.map((field) => (
                <Checkbox
                  key={field}
                  label={label(field)}
                  checked={current.includes(field)}
                  onCheckedChange={(checked) => {
                    const next = checked ? [...current, field] : current.filter((item) => item !== field);
                    update({
                      requirements: [
                        ...working.requirements.filter((row) => row.branch !== branch || row.state_id !== state.id),
                        { branch, state_id: state.id, fields: next },
                      ],
                    });
                  }}
                />
              ))}
            </fieldset>
          );
        })}
      </div>
      {message && <p role={conflict ? "alert" : "status"}>{message}</p>}
      {conflict && (
        <Button
          size="lg"
          stretch="auto"
          variant="secondary"
          onClick={async () => {
            const latest = await mutate();
            if (latest) {
              setDraft(null);
              setConflict(false);
              setMessage("");
            }
          }}
          label={label("reload")}
        />
      )}
      <div className="flex gap-3">
        <Button
          size="lg"
          stretch="auto"
          variant="secondary"
          disabled={busy}
          onClick={() => void save(true)}
          label={label("validate")}
        />
        <Button
          size="lg"
          stretch="auto"
          variant="primary"
          disabled={busy}
          onClick={() => void save(false)}
          label={label("save")}
        />
      </div>
    </section>
  );
}

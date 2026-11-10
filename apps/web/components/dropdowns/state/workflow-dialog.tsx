/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { Button } from "@makeplane/propel/components/button";
import { Dialog, DialogContent, DialogTitle } from "@makeplane/propel/components/dialog";
import { DateSelect } from "@plane/blocks/property-select";
import { useTranslation } from "@plane/i18n";
import type { TIssue } from "@plane/types";
import { getDate, renderFormattedPayloadDate } from "@plane/utils";
import { MemberSelect } from "@/components/dropdowns/member/member-select";

export function WorkflowFieldsDialog({
  projectId,
  initial,
  emptyRequirement,
  onSave,
  onClose,
}: {
  projectId: string;
  initial: Partial<TIssue>;
  emptyRequirement: boolean;
  onSave: (patch: Partial<TIssue>) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const label = (key: string) => t(`project_settings.workflow.${key}`);
  const [values, setValues] = useState<Partial<TIssue>>({
    assignee_ids: initial.assignee_ids ?? [],
    start_date: initial.start_date ?? null,
    target_date: initial.target_date ?? null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="sm">
        <div className="p-6">
          <DialogTitle>{label("complete_fields")}</DialogTitle>
          {emptyRequirement && <p>{label("empty_confirmation")}</p>}
          <form
            className="mt-4 flex flex-col gap-4"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError("");
              try {
                await onSave(values);
                onClose();
              } catch (failure) {
                setError((failure as { message?: string }).message ?? label("save_error"));
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>{label("assignees")}</label>
            <MemberSelect
              testId="workflow-assignees"
              projectId={projectId}
              value={values.assignee_ids ?? []}
              onChange={(ids) => setValues({ ...values, assignee_ids: ids })}
              multiple
              variant="pill-md"
            />
            <label>{label("start_date")}</label>
            <DateSelect
              testId="workflow-start-date"
              value={getDate(values.start_date) ?? null}
              onChange={(date) => setValues({ ...values, start_date: date ? renderFormattedPayloadDate(date) : null })}
              variant="pill-md"
              clearable
              placeholder={label("start_date")}
            />
            <label>{label("target_date")}</label>
            <DateSelect
              testId="workflow-due-date"
              value={getDate(values.target_date) ?? null}
              onChange={(date) => setValues({ ...values, target_date: date ? renderFormattedPayloadDate(date) : null })}
              variant="pill-md"
              clearable
              placeholder={label("target_date")}
            />
            {error && <p role="alert">{error}</p>}
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                size="lg"
                stretch="auto"
                label={label("cancel")}
                onClick={onClose}
              />
              <Button
                type="submit"
                variant="primary"
                size="lg"
                stretch="auto"
                label={label("apply_transition")}
                disabled={busy}
              />
            </div>
          </form>
        </div>
      </DialogContent>
    </Dialog>
  );
}

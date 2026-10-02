"use client";

import type { RefObject } from "react";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import type {
  WorkbenchSettingsContext,
  WorkbenchSettingsDescriptor,
} from "@/lib/types/workbench-settings";
import { WorkbenchSettingsContent } from "./workbench-settings-content";

interface WorkbenchSettingsSurfaceProps {
  descriptor: WorkbenchSettingsDescriptor;
  context: Omit<WorkbenchSettingsContext, "presentation">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bodyRef?: RefObject<HTMLDivElement | null>;
  testId?: string;
}

/** Apresenta um descriptor dentro da workbench que o abriu. */
export function WorkbenchSettingsSurface({
  descriptor,
  context,
  open,
  onOpenChange,
  bodyRef,
  testId,
}: WorkbenchSettingsSurfaceProps) {
  const resolvedContext: WorkbenchSettingsContext = {
    ...context,
    open,
    presentation: "workbench",
    onRequestClose: () => onOpenChange(false),
  };
  const bodyMode =
    descriptor.surface.workbench === "native-view" ? "fill" : "scroll";

  return (
    <WorkbenchDialog
      open={open}
      onOpenChange={onOpenChange}
      title={descriptor.title()}
      description={descriptor.description?.()}
      bodyRef={bodyRef}
      bodyMode={bodyMode}
      testId={testId}
    >
      <WorkbenchSettingsContent
        descriptor={descriptor}
        context={resolvedContext}
      />
    </WorkbenchDialog>
  );
}

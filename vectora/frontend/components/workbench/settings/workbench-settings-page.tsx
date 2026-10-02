"use client";

import { useState } from "react";
import type {
  WorkbenchSettingsContext,
  WorkbenchSettingsDescriptor,
  WorkbenchId,
} from "@/lib/types/workbench-settings";
import { m } from "@/lib/paraglide/messages";
import { WorkbenchSettingsContent } from "@/components/workbench/settings/workbench-settings-content";
import { WORKBENCH_TABS } from "@/lib/stores/workbench-store";

interface WorkbenchSettingsPageProps {
  descriptors: readonly WorkbenchSettingsDescriptor[];
  context: Omit<WorkbenchSettingsContext, "presentation">;
}

const WORKBENCH_LABELS: Record<WorkbenchId, () => string> = {
  context_graph: () => m.workbench_tab_context_graph(),
  storage: () => m.workbench_tab_storage(),
  tasks: () => m.workbench_tab_tasks(),
  browser: () => m.workbench_tab_browser(),
  git: () => m.workbench_tab_git(),
  terminal: () => m.workbench_tab_terminal(),
  files: () => m.workbench_tab_files(),
  plan: () => m.workbench_tab_plan(),
  library: () => m.workbench_tab_library(),
};

/** Página global que agrupa os mesmos descriptors por workbench. */
export function WorkbenchSettingsPage({
  descriptors,
  context,
}: WorkbenchSettingsPageProps) {
  const groups = descriptors.reduce<
    Map<WorkbenchId, WorkbenchSettingsDescriptor[]>
  >((result, descriptor) => {
    const group = result.get(descriptor.workbench) ?? [];
    group.push(descriptor);
    result.set(descriptor.workbench, group);
    return result;
  }, new Map());
  const orderedWorkbenches = WORKBENCH_TABS.filter((workbench) =>
    groups.has(workbench),
  );
  const [openWorkbench, setOpenWorkbench] = useState<WorkbenchId | null>(
    orderedWorkbenches[0] ?? null,
  );

  if (descriptors.length === 0) {
    return (
      <div className="flex min-h-32 items-center justify-center px-4 py-8 text-center text-sm text-muted-foreground">
        {m.workbench_settings_empty()}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 w-full flex-col gap-3 overflow-y-auto p-4">
      {orderedWorkbenches.map((workbench) => {
        const items = groups.get(workbench) ?? [];
        return (
          <details
            key={workbench}
            id={`workbench-settings-${workbench}`}
            className="group min-w-0 scroll-mt-4 rounded-lg border border-border/60"
            open={openWorkbench === workbench}
            onToggle={(event) => {
              setOpenWorkbench(event.currentTarget.open ? workbench : null);
            }}
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-base font-semibold [&::-webkit-details-marker]:hidden">
              <span className="break-words">
                {WORKBENCH_LABELS[workbench]()}
              </span>
              <span
                aria-hidden="true"
                className="text-muted-foreground transition-transform group-open:rotate-180"
              >
                ⌄
              </span>
            </summary>
            <div className="flex min-w-0 flex-col gap-4 border-t border-border/60 p-4">
              {items.map((descriptor) => {
                return (
                  <WorkbenchSettingsContent
                    key={descriptor.id}
                    descriptor={descriptor}
                    context={{ ...context, presentation: "settings" }}
                  />
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

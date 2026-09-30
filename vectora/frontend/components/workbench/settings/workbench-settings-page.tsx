"use client";

import type {
  WorkbenchSettingsContext,
  WorkbenchSettingsDescriptor,
  WorkbenchId,
} from "@/lib/types/workbench-settings";
import { m } from "@/lib/paraglide/messages";
import { WorkbenchSettingsContent } from "@/components/workbench/settings/workbench-settings-content";

interface WorkbenchSettingsPageProps {
  descriptors: readonly WorkbenchSettingsDescriptor[];
  context: Omit<WorkbenchSettingsContext, "presentation">;
}

const WORKBENCH_LABELS: Record<WorkbenchId, () => string> = {
  context_graph: () => m.workbench_tab_context_graph(),
  storage: () => m.workbench_tab_storage(),
  tasks: () => m.workbench_tab_tasks(),
  browser: () => m.workbench_tab_browser(),
  diff: () => m.workbench_tab_diff(),
  terminal: () => m.workbench_tab_terminal(),
  files: () => m.workbench_tab_files(),
};

/** Página global que agrupa os mesmos descriptors por workbench. */
export function WorkbenchSettingsPage({
  descriptors,
  context,
}: WorkbenchSettingsPageProps) {
  if (descriptors.length === 0) {
    return (
      <div className="flex min-h-32 items-center justify-center px-4 py-8 text-center text-sm text-muted-foreground">
        {m.workbench_settings_empty()}
      </div>
    );
  }

  const groups = descriptors.reduce<
    Map<WorkbenchId, WorkbenchSettingsDescriptor[]>
  >((result, descriptor) => {
    const group = result.get(descriptor.workbench) ?? [];
    group.push(descriptor);
    result.set(descriptor.workbench, group);
    return result;
  }, new Map());

  return (
    <div className="flex min-w-0 w-full flex-col gap-6 overflow-y-auto p-4">
      <nav aria-label={m.workbench_settings_page_aria()} className="min-w-0">
        <p className="mb-2 text-xs font-medium text-muted-foreground">
          {m.workbench_settings_page_index()}
        </p>
        <div className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-sm">
          {[...groups.keys()].map((workbench) => (
            <a
              key={workbench}
              href={`#workbench-settings-${workbench}`}
              className="break-words text-primary hover:underline"
            >
              {WORKBENCH_LABELS[workbench]()}
            </a>
          ))}
        </div>
      </nav>
      {[...groups.entries()].map(([workbench, items]) => (
        <section
          key={workbench}
          id={`workbench-settings-${workbench}`}
          className="min-w-0 scroll-mt-4"
        >
          <h2 className="mb-3 break-words text-base font-semibold">
            {WORKBENCH_LABELS[workbench]()}
          </h2>
          <div className="flex min-w-0 flex-col gap-4">
            {items.map((descriptor) => {
              return (
                <article
                  key={descriptor.id}
                  className="min-w-0 rounded-lg border border-border/60 p-4"
                >
                  <header className="mb-3 min-w-0">
                    <h3 className="break-words text-sm font-medium">
                      {descriptor.title()}
                    </h3>
                    {descriptor.description?.() && (
                      <p className="mt-1 break-words text-xs text-muted-foreground">
                        {descriptor.description?.()}
                      </p>
                    )}
                  </header>
                  <div className="min-w-0">
                    <WorkbenchSettingsContent
                      descriptor={descriptor}
                      context={{ ...context, presentation: "settings" }}
                    />
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

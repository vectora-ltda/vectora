"use client";

import { WorkbenchSettingsPage } from "@/components/workbench/settings/workbench-settings-page";
import { WORKBENCH_SETTINGS } from "@/components/workbench/settings/workbench-settings-registry";
import { useActiveWorkbenchContextStore } from "@/lib/stores/active-workbench-context-store";

/** Categoria global que apresenta os mesmos descriptors das workbenches. */
export function WorkbenchesSettings() {
  const threadId = useActiveWorkbenchContextStore((state) => state.threadId);
  const workspaceId = useActiveWorkbenchContextStore(
    (state) => state.workspaceId,
  );
  const browserProfileId = useActiveWorkbenchContextStore(
    (state) => state.browserProfileId,
  );
  return (
    <WorkbenchSettingsPage
      descriptors={WORKBENCH_SETTINGS}
      context={{
        threadId,
        workspaceId,
        browserProfileId,
      }}
    />
  );
}

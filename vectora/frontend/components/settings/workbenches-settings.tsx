"use client";

import { WorkbenchSettingsPage } from "@/components/workbench/settings/workbench-settings-page";
import { WORKBENCH_SETTINGS } from "@/components/workbench/settings/workbench-settings-registry";
import { useActiveWorkbenchContextStore } from "@/lib/stores/active-workbench-context-store";
import { useBrowserSettingsController } from "@/lib/stores/browser-settings-controller";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";

/** Categoria global que apresenta os mesmos descriptors das workbenches. */
export function WorkbenchesSettings() {
  const threadId = useActiveWorkbenchContextStore((state) => state.threadId);
  const workspaceId = useActiveWorkbenchContextStore(
    (state) => state.workspaceId,
  );
  const browserProfileId = useActiveWorkbenchContextStore(
    (state) => state.browserProfileId,
  );
  const requestOpenNativeSettings = useBrowserSettingsController(
    (state) => state.requestOpenNativeSettings,
  );
  const selectWorkbenchTab = useWorkbenchStore((state) => state.selectTab);
  const setSettingsOpen = useSettingsOverlayStore((state) => state.setOpen);

  return (
    <WorkbenchSettingsPage
      descriptors={WORKBENCH_SETTINGS}
      context={{
        threadId,
        workspaceId,
        browserProfileId,
        requestOpenNativeSettings: threadId
          ? () => {
              requestOpenNativeSettings({ threadId, workspaceId });
              selectWorkbenchTab(threadId, "browser");
              setSettingsOpen(false);
            }
          : undefined,
      }}
    />
  );
}

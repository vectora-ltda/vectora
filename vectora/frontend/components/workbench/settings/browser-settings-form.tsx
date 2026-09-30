"use client";

import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useToastStore } from "@/lib/stores/toast-store";
import { resolveBrowserProfileId } from "@/lib/browser-profile";
import { m } from "@/lib/paraglide/messages";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";

interface BrowserSettingsFormProps extends WorkbenchSettingsContext {
  settingsViewError?: boolean;
  onRetry?: () => void;
}

/** Configurações persistentes do perfil do Browser, reutilizadas no modal e no Settings. */
export function BrowserSettingsForm({
  threadId,
  workspaceId,
  browserProfileId,
  requestOpenNativeSettings,
  settingsViewError = false,
  onRetry,
}: BrowserSettingsFormProps) {
  const [clearProfileError, setClearProfileError] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const desktopBrowser =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  const profileId =
    browserProfileId ?? resolveBrowserProfileId(threadId, workspaceId);

  return (
    <div className="min-w-0 space-y-3 p-4 text-xs text-muted-foreground">
      <p>{m.workbench_browser_settings_description()}</p>
      {desktopBrowser ? (
        <>
          {requestOpenNativeSettings ? (
            <button
              type="button"
              className="rounded border border-border px-2 py-1 text-left hover:bg-accent"
              onClick={requestOpenNativeSettings}
            >
              {m.workbench_browser_settings_toggle()}
            </button>
          ) : null}
          <button
            type="button"
            className="max-w-full rounded border border-destructive/40 px-2 py-1 text-left text-destructive hover:bg-destructive/10"
            onClick={() => setConfirmClear(true)}
          >
            {m.workbench_browser_clear_profile_data()}
          </button>
          <ConfirmDialog
            open={confirmClear}
            title={m.workbench_browser_clear_profile_data()}
            description={m.workbench_browser_clear_profile_confirm()}
            confirmLabel={m.workbench_browser_clear_profile_data()}
            variant="destructive"
            onCancel={() => setConfirmClear(false)}
            onConfirm={async () => {
              setConfirmClear(false);
              setClearProfileError(false);
              try {
                await desktopBrowser.clearProfileData(profileId ?? undefined);
              } catch {
                setClearProfileError(true);
                return;
              }
              useToastStore
                .getState()
                .success(m.workbench_browser_clear_profile_success());
            }}
          />
          {clearProfileError ? (
            <p role="alert" className="text-destructive">
              {m.workbench_browser_clear_profile_error()}
            </p>
          ) : null}
          {settingsViewError ? (
            <div role="alert" className="space-y-2 text-destructive">
              <p>{m.workbench_browser_settings_error()}</p>
              {onRetry ? (
                <button
                  type="button"
                  className="rounded border border-destructive/40 px-2 py-1 hover:bg-destructive/10"
                  onClick={onRetry}
                >
                  {m.workbench_browser_settings_toggle()}
                </button>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <p>{m.workbench_browser_settings_unavailable()}</p>
      )}
    </div>
  );
}

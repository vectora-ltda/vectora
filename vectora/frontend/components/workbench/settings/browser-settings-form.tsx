"use client";

import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useToastStore } from "@/lib/stores/toast-store";
import { resolveBrowserProfileId } from "@/lib/browser-profile";
import { clearBrowserSessionHistory } from "@/lib/browser-session-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { m } from "@/lib/paraglide/messages";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";

interface BrowserSettingsFormProps extends WorkbenchSettingsContext {}

/** Configurações persistentes do perfil do Browser, reutilizadas no modal e no Settings. */
export function BrowserSettingsForm({
  threadId,
  workspaceId,
  browserProfileId,
}: BrowserSettingsFormProps) {
  const [clearProfileError, setClearProfileError] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const allowPopups = useSettingsStore((s) => s.browserAllowPopups);
  const zoomPercent = useSettingsStore((s) => s.browserZoomPercent);
  const setAllowPopups = useSettingsStore((s) => s.setBrowserAllowPopups);
  const setZoomPercent = useSettingsStore((s) => s.setBrowserZoomPercent);
  const permissionMode = useSettingsStore((s) => s.browserPermissionMode);
  const setPermissionMode = useSettingsStore((s) => s.setBrowserPermissionMode);
  const desktopBrowser =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  const profileId =
    browserProfileId ?? resolveBrowserProfileId(threadId, workspaceId);
  const sessionKey = `${workspaceId ?? ""}:${threadId ?? ""}`;

  return (
    <div className="min-w-0 space-y-3 p-4 text-xs text-muted-foreground">
      <p>{m.workbench_browser_settings_description()}</p>
      {desktopBrowser ? (
        <>
          <p>{m.workbench_browser_settings_local_notice()}</p>
          <label className="flex items-start gap-2 rounded border border-border/60 p-2 text-foreground">
            <input
              type="checkbox"
              checked={allowPopups}
              onChange={(event) => setAllowPopups(event.target.checked)}
            />
            <span>
              <span className="block font-medium">
                {m.workbench_browser_popups_label()}
              </span>
              <span className="block text-muted-foreground">
                {m.workbench_browser_popups_help()}
              </span>
            </span>
          </label>
          <label className="flex items-center justify-between gap-3 rounded border border-border/60 p-2 text-foreground">
            <span>
              <span className="block font-medium">
                {m.workbench_browser_zoom_label()}
              </span>
              <span className="block text-muted-foreground">
                {m.workbench_browser_zoom_help()}
              </span>
            </span>
            <input
              className="w-20 rounded border border-border/60 bg-background px-2 py-1 text-right"
              type="number"
              min={25}
              max={500}
              step={5}
              value={zoomPercent}
              onChange={(event) => setZoomPercent(Number(event.target.value))}
            />
          </label>
          <div className="flex items-center justify-between gap-3 rounded border border-border/60 p-2 text-foreground">
            <span>
              <span className="block font-medium">
                {m.workbench_browser_permissions_label()}
              </span>
              <span className="block text-muted-foreground">
                {m.workbench_browser_permissions_help()}
              </span>
            </span>
            <Select
              value={permissionMode}
              onValueChange={(value) =>
                setPermissionMode(value as "allow" | "deny")
              }
            >
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="allow">
                  {m.workbench_browser_permissions_allow()}
                </SelectItem>
                <SelectItem value="deny">
                  {m.workbench_browser_permissions_deny()}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <button
            type="button"
            className="max-w-full rounded border border-destructive/40 px-2 py-1 text-left text-destructive hover:bg-destructive/10"
            onClick={() => setConfirmClear(true)}
          >
            {m.workbench_browser_clear_profile_data()}
          </button>
          <button
            type="button"
            className="max-w-full rounded border border-border/60 px-2 py-1 text-left text-foreground hover:bg-muted/40"
            onClick={() => clearBrowserSessionHistory(sessionKey)}
          >
            {m.workbench_browser_clear_history()}
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
                clearBrowserSessionHistory(sessionKey);
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
        </>
      ) : (
        <p>{m.workbench_browser_settings_unavailable()}</p>
      )}
    </div>
  );
}

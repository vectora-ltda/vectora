import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { m } from "@/lib/paraglide/messages";
import { useToastStore } from "@/lib/stores/toast-store";
import { clearBrowserSessionHistory } from "@/lib/browser-session-store";

interface BrowserProfileCleanupActionProps {
  profileId: string | null;
  sessionKey: string;
  clearProfileData?: (
    profileId?: string,
    options?: { storage: boolean; cache: boolean; credentials: boolean },
  ) => Promise<void>;
}

/** Destructive profile cleanup kept beside browser actions, outside settings descriptors. */
export function BrowserProfileCleanupAction({
  profileId,
  sessionKey,
  clearProfileData,
}: BrowserProfileCleanupActionProps) {
  const [open, setOpen] = useState(false);
  const [clearStorage, setClearStorage] = useState(true);
  const [clearCache, setClearCache] = useState(true);
  const [clearCredentials, setClearCredentials] = useState(false);
  const [error, setError] = useState(false);

  if (!clearProfileData) return null;

  return (
    <>
      <button
        type="button"
        data-testid="browser-clear-profile-btn"
        className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        title={m.workbench_browser_clear_profile_data()}
        aria-label={m.workbench_browser_clear_profile_data()}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true">⌫</span>
      </button>
      <ConfirmDialog
        open={open}
        title={m.workbench_browser_clear_profile_data()}
        description={m.workbench_browser_clear_profile_confirm()}
        confirmLabel={m.workbench_browser_clear_profile_data()}
        variant="destructive"
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          setOpen(false);
          setError(false);
          try {
            await clearProfileData(profileId ?? undefined, {
              storage: clearStorage,
              cache: clearCache,
              credentials: clearCredentials,
            });
            clearBrowserSessionHistory(sessionKey);
            useToastStore
              .getState()
              .success(m.workbench_browser_clear_profile_success());
          } catch {
            setError(true);
          }
        }}
      >
        <div className="space-y-2 rounded border border-border/60 p-2 text-sm">
          <p className="font-medium">
            {m.workbench_browser_clear_scope_label()}
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={clearStorage}
              onChange={(event) => setClearStorage(event.target.checked)}
            />
            {m.workbench_browser_clear_storage_label()}
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={clearCache}
              onChange={(event) => setClearCache(event.target.checked)}
            />
            {m.workbench_browser_clear_cache_label()}
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={clearCredentials}
              onChange={(event) => setClearCredentials(event.target.checked)}
            />
            {m.workbench_browser_clear_credentials_label()}
          </label>
        </div>
      </ConfirmDialog>
      {error ? (
        <span role="alert" className="sr-only">
          {m.workbench_browser_clear_profile_error()}
        </span>
      ) : null}
    </>
  );
}

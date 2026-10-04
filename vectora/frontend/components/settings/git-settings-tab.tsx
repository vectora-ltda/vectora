"use client";

import { useEffect, useState } from "react";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";
import { Input } from "@/components/ui/input";

export function GitSettingsTab({
  showHeading = true,
}: {
  showHeading?: boolean;
}) {
  const hooks = useSettingsStore((s) => s.gitHooksEnabled);
  const signoff = useSettingsStore((s) => s.gitSignoffEnabled);
  const bypass = useSettingsStore((s) => s.gitBypassEnabled);
  const autoFetch = useSettingsStore((s) => s.gitAutoFetchEnabled);
  const autoFetchInterval = useSettingsStore(
    (s) => s.gitAutoFetchIntervalSeconds,
  );
  const setHooks = useSettingsStore((s) => s.setGitHooksEnabled);
  const setSignoff = useSettingsStore((s) => s.setGitSignoffEnabled);
  const setBypass = useSettingsStore((s) => s.setGitBypassEnabled);
  const setAutoFetch = useSettingsStore((s) => s.setGitAutoFetchEnabled);
  const setAutoFetchInterval = useSettingsStore(
    (s) => s.setGitAutoFetchIntervalSeconds,
  );
  const [autoFetchIntervalDraft, setAutoFetchIntervalDraft] = useState(
    String(autoFetchInterval),
  );
  useEffect(() => {
    setAutoFetchIntervalDraft(String(autoFetchInterval));
  }, [autoFetchInterval]);
  return (
    <div className="space-y-5 max-w-xl">
      {showHeading ? (
        <div>
          <h2 className="text-base font-semibold">
            {m.settings_category_git()}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {m.settings_git_description()}
          </p>
        </div>
      ) : null}
      <label className="flex items-start gap-3 rounded-md border border-border/60 p-3">
        <input
          type="checkbox"
          checked={hooks}
          onChange={(e) => setHooks(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <span className="block text-sm font-medium">
            {m.settings_git_hooks()}
          </span>
          <span className="block text-xs text-muted-foreground">
            {m.settings_git_hooks_description()}
          </span>
        </span>
      </label>
      <label className="flex items-start gap-3 rounded-md border border-border/60 p-3">
        <input
          type="checkbox"
          checked={autoFetch}
          onChange={(e) => setAutoFetch(e.target.checked)}
          className="mt-0.5"
        />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">
            {m.settings_git_auto_fetch()}
          </span>
          <span className="block text-xs text-muted-foreground">
            {m.settings_git_auto_fetch_description()}
          </span>
          {autoFetch ? (
            <span className="mt-2 flex items-center gap-2">
              <Input
                aria-label={m.settings_git_auto_fetch_interval()}
                type="number"
                min={30}
                max={3600}
                step={30}
                value={autoFetchIntervalDraft}
                onChange={(e) => setAutoFetchIntervalDraft(e.target.value)}
                onBlur={() => {
                  const draft = autoFetchIntervalDraft.trim();
                  if (draft === "" || !Number.isFinite(Number(draft))) {
                    setAutoFetchIntervalDraft(String(autoFetchInterval));
                    return;
                  }
                  setAutoFetchInterval(Number(draft));
                }}
                className="w-24"
              />
              <span className="text-xs text-muted-foreground">
                {m.settings_git_seconds()}
              </span>
            </span>
          ) : null}
        </span>
      </label>
      <label className="flex items-start gap-3 rounded-md border border-border/60 p-3">
        <input
          type="checkbox"
          checked={signoff}
          onChange={(e) => setSignoff(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <span className="block text-sm font-medium">
            {m.settings_git_signoff()}
          </span>
          <span className="block text-xs text-muted-foreground">
            {m.settings_git_signoff_description()}
          </span>
        </span>
      </label>
      <label className="flex items-start gap-3 rounded-md border border-border/60 p-3">
        <input
          type="checkbox"
          checked={bypass}
          onChange={(e) => setBypass(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <span className="block text-sm font-medium">
            {m.settings_git_bypass()}
          </span>
          <span className="block text-xs text-muted-foreground">
            {m.settings_git_bypass_description()}
          </span>
        </span>
      </label>
    </div>
  );
}

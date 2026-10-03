"use client";

import { useEffect, useState } from "react";
import {
  getBrowserProfileSettings,
  useSettingsStore,
} from "@/lib/stores/settings-store";
import { clearBrowserSessionHistory } from "@/lib/browser-session-store";
import { m } from "@/lib/paraglide/messages";
import type {
  VectoraBrowserCookie,
  VectoraBrowserCredential,
  VectoraBrowserDownloadEvent,
} from "@/lib/types/vectora-bridge";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";

function Toggle({
  id,
  label,
  help,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label htmlFor={id} className="flex items-start justify-between gap-3">
      <span className="min-w-0">
        <span className="block font-medium">{label}</span>
        <span className="block text-xs text-muted-foreground">{help}</span>
      </span>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 accent-[var(--color-primary)]"
      />
    </label>
  );
}

/** Vectora-owned Browser settings; it never depends on Chrome's WebUI. */
export function BrowserSettingsForm(context: WorkbenchSettingsContext) {
  const settings = useSettingsStore();
  const bridge =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  const profileId = context.browserProfileId ?? null;
  const profileSettings = getBrowserProfileSettings(settings, profileId);
  const sessionKey = `${context.workspaceId ?? ""}:${context.threadId ?? ""}`;
  const [credentials, setCredentials] = useState<VectoraBrowserCredential[]>(
    [],
  );
  const [cookies, setCookies] = useState<VectoraBrowserCookie[]>([]);
  const [downloads, setDownloads] = useState<VectoraBrowserDownloadEvent[]>([]);
  const [origin, setOrigin] = useState("");
  const [credentialOrigin, setCredentialOrigin] = useState("");
  const [credentialUsername, setCredentialUsername] = useState("");
  const [credentialPassword, setCredentialPassword] = useState("");
  const [clearStorage, setClearStorage] = useState(true);
  const [clearCache, setClearCache] = useState(true);
  const [clearCredentials, setClearCredentials] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!bridge || !profileId) return;
    let cancelled = false;
    void Promise.all([
      bridge.listCredentials?.(profileId) ?? Promise.resolve([]),
      bridge.listCookies?.(profileId) ?? Promise.resolve([]),
    ])
      .then(([nextCredentials, nextCookies]) => {
        if (cancelled) return;
        setCredentials(nextCredentials);
        setCookies(nextCookies);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    const unsubscribe = bridge.onDownload?.((download) => {
      if (download.profileId !== profileId) return;
      setDownloads((current) =>
        [...current.filter((item) => item.id !== download.id), download].slice(
          -20,
        ),
      );
    });
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [bridge, profileId]);

  const addOriginPermission = () => {
    const normalized = origin.trim().replace(/\/$/, "");
    if (!/^https?:\/\/[^/]+$/i.test(normalized)) return;
    if (profileId) {
      settings.setBrowserProfileSettings(profileId, {
        originPermissions: { [normalized]: "allow" },
      });
    } else {
      settings.setBrowserOriginPermission(normalized, "allow");
    }
    setOrigin("");
  };

  const saveCredential = async () => {
    if (!bridge?.saveCredential || !profileId) return;
    try {
      const saved = await bridge.saveCredential({
        profileId,
        origin: credentialOrigin.trim().replace(/\/$/, ""),
        username: credentialUsername,
        password: credentialPassword,
      });
      setCredentials((current) => [
        ...current.filter((item) => item.id !== saved.id),
        saved,
      ]);
      setCredentialPassword("");
    } catch {
      setError(true);
    }
  };

  const removeCredential = async (id: string) => {
    if (!bridge?.deleteCredential || !profileId) return;
    try {
      await bridge.deleteCredential({ profileId, id });
      setCredentials((current) => current.filter((item) => item.id !== id));
    } catch {
      setError(true);
    }
  };

  const removeCookie = async (cookie: VectoraBrowserCookie) => {
    if (!bridge?.removeCookie || !profileId) return;
    const domain = cookie.domain.replace(/^\./, "");
    try {
      await bridge.removeCookie({
        profileId,
        url: `http${cookie.secure ? "s" : ""}://${domain}${cookie.path}`,
        name: cookie.name,
      });
      setCookies((current) =>
        current.filter(
          (item) =>
            item.name !== cookie.name ||
            item.domain !== cookie.domain ||
            item.path !== cookie.path,
        ),
      );
    } catch {
      setError(true);
    }
  };

  const clearProfileData = async () => {
    if (!bridge?.clearProfileData || !profileId) return;
    try {
      await bridge.clearProfileData(profileId, {
        storage: clearStorage,
        cache: clearCache,
        credentials: clearCredentials,
      });
      if (clearStorage || clearCache) {
        clearBrowserSessionHistory(sessionKey);
        setCookies([]);
      }
      if (clearCredentials) setCredentials([]);
    } catch {
      setError(true);
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-5 text-sm">
      <p className="text-xs text-muted-foreground">
        {m.workbench_browser_settings_local_notice()}
      </p>
      {!bridge && (
        <p className="rounded border border-border/60 p-3 text-xs text-muted-foreground">
          {m.workbench_browser_web_runtime_notice()}
        </p>
      )}
      <section className="space-y-3">
        <Toggle
          id="browser-allow-popups"
          label={m.workbench_browser_popups_label()}
          help={m.workbench_browser_popups_help()}
          checked={profileSettings.allowPopups}
          onChange={(value) =>
            profileId
              ? settings.setBrowserProfileSettings(profileId, {
                  allowPopups: value,
                })
              : settings.setBrowserAllowPopups(value)
          }
        />
        <label className="flex items-center justify-between gap-3">
          <span>
            <span className="block font-medium">
              {m.workbench_browser_zoom_label()}
            </span>
            <span className="block text-xs text-muted-foreground">
              {m.workbench_browser_zoom_help()}
            </span>
          </span>
          <input
            aria-label={m.workbench_browser_zoom_label()}
            type="number"
            min={25}
            max={500}
            step={10}
            value={profileSettings.zoomPercent}
            onChange={(event) =>
              profileId
                ? settings.setBrowserProfileSettings(profileId, {
                    zoomPercent: Number(event.target.value),
                  })
                : settings.setBrowserZoomPercent(Number(event.target.value))
            }
            className="w-20 rounded border border-border/60 bg-background px-2 py-1"
          />
        </label>
        <label className="flex items-center justify-between gap-3">
          <span>
            <span className="block font-medium">
              {m.workbench_browser_search_engine_label()}
            </span>
            <span className="block text-xs text-muted-foreground">
              {m.workbench_browser_search_engine_help()}
            </span>
          </span>
          <select
            aria-label={m.workbench_browser_search_engine_label()}
            value={profileSettings.searchEngine}
            onChange={(event) =>
              profileId
                ? settings.setBrowserProfileSettings(profileId, {
                    searchEngine: event.target.value as
                      "duckduckgo" | "google" | "bing",
                  })
                : settings.setBrowserSearchEngine(
                    event.target.value as "duckduckgo" | "google" | "bing",
                  )
            }
            className="rounded border border-border/60 bg-background px-2 py-1"
          >
            <option value="duckduckgo">
              {m.workbench_browser_search_engine_duckduckgo()}
            </option>
            <option value="google">
              {m.workbench_browser_search_engine_google()}
            </option>
            <option value="bing">
              {m.workbench_browser_search_engine_bing()}
            </option>
          </select>
        </label>
      </section>
      <section className="space-y-2">
        <h3 className="font-medium">
          {m.workbench_browser_permissions_label()}
        </h3>
        <p className="text-xs text-muted-foreground">
          {m.workbench_browser_permissions_help()}
        </p>
        <select
          aria-label={m.workbench_browser_permissions_label()}
          value={profileSettings.permissionMode}
          onChange={(event) =>
            profileId
              ? settings.setBrowserProfileSettings(profileId, {
                  permissionMode: event.target.value as "allow" | "deny",
                })
              : settings.setBrowserPermissionMode(
                  event.target.value as "allow" | "deny",
                )
          }
          className="rounded border border-border/60 bg-background px-2 py-1"
        >
          <option value="deny">{m.workbench_browser_permissions_deny()}</option>
          <option value="allow">
            {m.workbench_browser_permissions_allow()}
          </option>
        </select>
        <p className="text-xs font-medium">
          {m.workbench_browser_origin_permissions_label()}
        </p>
        <p className="text-xs text-muted-foreground">
          {m.workbench_browser_origin_permissions_help()}
        </p>
        <div className="flex min-w-0 gap-2">
          <input
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            placeholder={m.workbench_browser_origin_permissions_placeholder()}
            className="min-w-0 flex-1 rounded border border-border/60 bg-background px-2 py-1"
          />
          <button
            type="button"
            onClick={addOriginPermission}
            className="rounded border border-border/60 px-2 py-1 hover:bg-muted/40"
          >
            {m.workbench_browser_origin_permissions_add()}
          </button>
        </div>
        {Object.entries(profileSettings.originPermissions).map(
          ([site, mode]) => (
            <div key={site} className="flex items-center justify-between gap-2">
              <span className="truncate text-xs">{site}</span>
              <span className="text-xs text-muted-foreground">{mode}</span>
              <button
                type="button"
                onClick={() => {
                  if (!profileId) {
                    settings.removeBrowserOriginPermission(site);
                    return;
                  }
                  const originPermissions = {
                    ...profileSettings.originPermissions,
                  };
                  delete originPermissions[site];
                  settings.setBrowserProfileSettings(profileId, {
                    originPermissions,
                  });
                }}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                {m.workbench_browser_password_remove()}
              </button>
            </div>
          ),
        )}
      </section>
      {bridge && profileId && (
        <>
          <section className="space-y-2">
            <h3 className="font-medium">
              {m.workbench_browser_password_manager_title()}
            </h3>
            <p className="text-xs text-muted-foreground">
              {m.workbench_browser_password_manager_help()}
            </p>
            <div className="grid min-w-0 gap-2 sm:grid-cols-3">
              <input
                value={credentialOrigin}
                onChange={(event) => setCredentialOrigin(event.target.value)}
                placeholder={m.workbench_browser_password_origin()}
                className="min-w-0 rounded border border-border/60 bg-background px-2 py-1"
              />
              <input
                value={credentialUsername}
                onChange={(event) => setCredentialUsername(event.target.value)}
                placeholder={m.workbench_browser_password_username()}
                className="min-w-0 rounded border border-border/60 bg-background px-2 py-1"
              />
              <input
                type="password"
                value={credentialPassword}
                onChange={(event) => setCredentialPassword(event.target.value)}
                placeholder={m.workbench_browser_password_secret()}
                className="min-w-0 rounded border border-border/60 bg-background px-2 py-1"
              />
            </div>
            <button
              type="button"
              onClick={() => void saveCredential()}
              className="rounded border border-border/60 px-2 py-1 hover:bg-muted/40"
            >
              {m.workbench_browser_password_save()}
            </button>
            {credentials.map((credential) => (
              <div
                key={credential.id}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="min-w-0 truncate">
                  {credential.origin} · {credential.username}
                </span>
                <button
                  type="button"
                  onClick={() => void removeCredential(credential.id)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  {m.workbench_browser_password_remove()}
                </button>
              </div>
            ))}
          </section>
          <section className="space-y-2">
            <h3 className="font-medium">
              {m.workbench_browser_cookies_title()}
            </h3>
            <p className="text-xs text-muted-foreground">
              {m.workbench_browser_cookies_help()}
            </p>
            {cookies.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {m.workbench_browser_cookies_empty()}
              </p>
            ) : (
              cookies.map((cookie) => (
                <div
                  key={`${cookie.domain}:${cookie.path}:${cookie.name}`}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="min-w-0 truncate">
                    {cookie.name} · {cookie.domain}
                  </span>
                  <button
                    type="button"
                    onClick={() => void removeCookie(cookie)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {m.workbench_browser_cookies_remove()}
                  </button>
                </div>
              ))
            )}
          </section>
          <section className="space-y-2">
            <h3 className="font-medium">
              {m.workbench_browser_downloads_label()}
            </h3>
            <p className="text-xs text-muted-foreground">
              {m.workbench_browser_downloads_help()}
            </p>
            {downloads.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {m.workbench_browser_downloads_empty()}
              </p>
            ) : (
              downloads.map((download) => (
                <div
                  key={download.id}
                  className="flex justify-between gap-2 text-xs"
                >
                  <span className="min-w-0 truncate">{download.filename}</span>
                  <span className="text-muted-foreground">
                    {download.state === "progressing"
                      ? m.workbench_browser_downloads_in_progress()
                      : download.state === "completed"
                        ? m.workbench_browser_downloads_completed()
                        : m.workbench_browser_downloads_failed()}
                  </span>
                </div>
              ))
            )}
          </section>
          <fieldset className="space-y-2 rounded border border-border/60 p-3">
            <legend className="px-1 text-xs font-medium">
              {m.workbench_browser_clear_scope_label()}
            </legend>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={clearStorage}
                onChange={(event) => setClearStorage(event.target.checked)}
              />
              {m.workbench_browser_clear_storage_label()}
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={clearCache}
                onChange={(event) => setClearCache(event.target.checked)}
              />
              {m.workbench_browser_clear_cache_label()}
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={clearCredentials}
                onChange={(event) => setClearCredentials(event.target.checked)}
              />
              {m.workbench_browser_clear_credentials_label()}
            </label>
          </fieldset>
          <button
            type="button"
            onClick={() => void clearProfileData()}
            className="self-start rounded border border-destructive/60 px-2 py-1 text-destructive hover:bg-destructive/10"
          >
            {m.workbench_browser_clear_profile_data()}
          </button>
          <button
            type="button"
            onClick={() => clearBrowserSessionHistory(sessionKey)}
            className="self-start rounded border border-border/60 px-2 py-1 hover:bg-muted/40"
          >
            {m.workbench_browser_clear_history()}
          </button>
        </>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {m.workbench_browser_settings_error()}
        </p>
      )}
    </div>
  );
}

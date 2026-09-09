"use client";

/**
 * Banner de update do Electron — invisível no browser puro.
 *
 * Subscreve a ``window.vectora.onUpdateStatus`` (bridge do preload.ts).
 * Expõe o fluxo explícito: disponibilidade, download aprovado e reinício.
 */

import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { m } from "@/lib/paraglide/messages";

export function UpdateBanner() {
  const [state, setState] = useState<
    | "available"
    | "downloading"
    | "downloaded"
    | "error"
    | "checking"
    | "not-available"
  >();
  const [version, setVersion] = useState<string>("");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [changelog, setChangelog] = useState("");

  useEffect(() => {
    if (typeof window === "undefined" || !window.vectora?.onUpdateStatus) {
      return;
    }
    const unsubscribe = window.vectora.onUpdateStatus((status) => {
      if (status.state === "checking" || status.state === "not-available") {
        setState(undefined);
        setError("");
        setProgress(0);
      } else if (status.state === "available") {
        setState("available");
        setError("");
        setChangelog(status.changelog ?? "");
        setProgress(0);
        if (status.message) setVersion(status.message);
      } else if (status.state === "downloading") {
        setState("downloading");
        setProgress(status.progress ?? 0);
      } else if (status.state === "downloaded") setState("downloaded");
      else if (status.state === "error") {
        setState("error");
        setError(status.message ?? "");
      }
    });
    return unsubscribe;
  }, []);

  if (!state) return null;

  const download = () => {
    setState("downloading");
    window.vectora?.downloadUpdate?.();
  };

  return (
    <div className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-b border-emerald-500/30 px-4 py-2 text-xs flex items-center gap-2">
      <Download className="w-3.5 h-3.5 shrink-0" />
      <span className="flex-1 min-w-0">
        {state === "available" &&
          m.update_banner_available({ v: version || "" })}
        {state === "downloading" &&
          m.update_banner_downloading({ p: Math.round(progress) })}
        {state === "downloaded" &&
          (version
            ? m.update_banner_ready_with_version({ v: version })
            : m.update_banner_ready())}
        {state === "error" && m.update_banner_error({ e: error })}
        {(state === "available" || state === "error") && changelog && (
          <details className="mt-1 opacity-90">
            <summary>{m.update_banner_changelog()}</summary>
            <p className="whitespace-pre-wrap mt-1">{changelog}</p>
          </details>
        )}
      </span>
      {(state === "available" || state === "error") && (
        <button
          type="button"
          onClick={download}
          className="px-2 py-0.5 rounded border border-current/40 hover:bg-current/10 transition-colors"
        >
          {m.update_banner_download()}
        </button>
      )}
      {state === "downloaded" && (
        <button
          type="button"
          onClick={() => window.vectora?.quitAndInstallUpdate?.()}
          className="px-2 py-0.5 rounded border border-current/40 hover:bg-current/10 transition-colors"
        >
          {m.update_banner_restart()}
        </button>
      )}
    </div>
  );
}

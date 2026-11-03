"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Play, RefreshCw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { m as msg } from "@/lib/paraglide/messages";

interface DevtoolsTab {
  tab_id: string;
  url: string;
  active: boolean;
}

interface ConsoleMessage {
  type: string;
  text: string;
}

interface NetworkRequest {
  request_id: string;
  url: string;
  method: string;
  resource_type: string;
  status: number | string | null;
  error?: string | null;
}

type DevtoolsSubTab = "console" | "network" | "elements";

interface BrowserDevtoolsPanelProps {
  wsId: string;
  onClose: () => void;
}

/** Painel de observabilidade da sessão de browser do AGENTE (Playwright
 * conectado ao Chromium do Electron via CDP, backend/browser/session.py) — distinto do resto da aba Browser,
 * que mostra a página que o USUÁRIO navega (iframe/WebContentsView). Sem
 * este painel, console/network/DOM do que o agente vê ficam invisíveis ao
 * humano, só acessíveis via tool call. */
export function BrowserDevtoolsPanel({
  wsId,
  onClose,
}: BrowserDevtoolsPanelProps) {
  const [subTab, setSubTab] = useState<DevtoolsSubTab>("console");
  const [tabs, setTabs] = useState<DevtoolsTab[]>([]);
  const [selectedTabId, setSelectedTabId] = useState<string | null>(null);
  const [consoleMessages, setConsoleMessages] = useState<ConsoleMessage[]>([]);
  const [networkRequests, setNetworkRequests] = useState<NetworkRequest[]>([]);
  const [script, setScript] = useState("");
  const [evalResult, setEvalResult] = useState<string | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [evalLoading, setEvalLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const tabQuery = selectedTabId
    ? `?tab_id=${encodeURIComponent(selectedTabId)}`
    : "";

  const fetchTabs = useCallback(async () => {
    if (!wsId) return;
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/browser/devtools/tabs`,
      );
      if (res.ok) {
        const data = (await res.json()) as { tabs: DevtoolsTab[] };
        setTabs(data.tabs ?? []);
        setSelectedTabId((prev) => {
          if (prev && data.tabs.some((t) => t.tab_id === prev)) return prev;
          return (
            data.tabs.find((t) => t.active)?.tab_id ??
            data.tabs[0]?.tab_id ??
            null
          );
        });
      }
    } catch {
      // silently ignore
    }
  }, [wsId]);

  const fetchConsole = useCallback(async () => {
    if (!wsId) return;
    setLoading(true);
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/browser/devtools/console${tabQuery}`,
      );
      if (res.ok) {
        const data = (await res.json()) as { messages: ConsoleMessage[] };
        setConsoleMessages(data.messages ?? []);
      }
    } catch {
      // silently ignore
    } finally {
      setLoading(false);
    }
  }, [wsId, tabQuery]);

  const fetchNetwork = useCallback(async () => {
    if (!wsId) return;
    setLoading(true);
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/browser/devtools/network${tabQuery}`,
      );
      if (res.ok) {
        const data = (await res.json()) as { requests: NetworkRequest[] };
        setNetworkRequests(data.requests ?? []);
      }
    } catch {
      // silently ignore
    } finally {
      setLoading(false);
    }
  }, [wsId, tabQuery]);

  const clearConsole = useCallback(async () => {
    if (!wsId) return;
    await fetch(
      `/workspaces/${encodeURIComponent(wsId)}/browser/devtools/console${tabQuery}`,
      { method: "DELETE" },
    );
    setConsoleMessages([]);
  }, [wsId, tabQuery]);

  const runScript = useCallback(async () => {
    if (!wsId || !script.trim()) return;
    setEvalLoading(true);
    setEvalError(null);
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/browser/devtools/evaluate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ script, tab_id: selectedTabId }),
        },
      );
      const data = (await res.json()) as {
        status: string;
        result?: unknown;
        error?: string | null;
      };
      if (data.status === "ok") {
        setEvalResult(JSON.stringify(data.result, null, 2));
      } else {
        setEvalError(data.error ?? "erro desconhecido");
      }
    } catch (err) {
      setEvalError(err instanceof Error ? err.message : String(err));
    } finally {
      setEvalLoading(false);
    }
  }, [wsId, script, selectedTabId]);

  useEffect(() => {
    // Busca as abas do navegador remoto (rede) ao montar, não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    fetchTabs();
  }, [fetchTabs]);

  useEffect(() => {
    const poll =
      subTab === "console"
        ? fetchConsole
        : subTab === "network"
          ? fetchNetwork
          : null;
    if (!poll) return;
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [subTab, fetchConsole, fetchNetwork]);

  useEffect(() => {
    if (consoleMessages.length === 0) return;
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [consoleMessages]);

  const refresh = () => {
    fetchTabs();
    if (subTab === "console") fetchConsole();
    else if (subTab === "network") fetchNetwork();
  };

  return (
    <div className="flex h-64 shrink-0 flex-col border-t border-border/60 bg-background">
      <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-2 py-1.5">
        <div className="flex items-center gap-1">
          {(["console", "network", "elements"] as const).map((t) => (
            <button
              key={t}
              data-testid={`devtools-subtab-${t}`}
              onClick={() => setSubTab(t)}
              className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                subTab === t
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {t === "console"
                ? msg.workbench_browser_devtools_tab_console()
                : t === "network"
                  ? msg.workbench_browser_devtools_tab_network()
                  : msg.workbench_browser_devtools_tab_elements()}
            </button>
          ))}
          {tabs.length > 1 && (
            <select
              aria-label={msg.workbench_browser_devtools_tab_selector()}
              value={selectedTabId ?? ""}
              onChange={(e) => setSelectedTabId(e.target.value)}
              className="ml-1 h-6 rounded border border-border/40 bg-background/80 text-[10px]"
            >
              {tabs.map((t) => (
                <option key={t.tab_id} value={t.tab_id}>
                  {t.url || t.tab_id}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="flex items-center gap-1">
          {subTab === "console" && (
            <button
              onClick={clearConsole}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              title={msg.workbench_browser_devtools_console_clear()}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            onClick={refresh}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
            title={msg.workbench_browser_devtools_refresh()}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            />
          </button>
          <button
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
            title={msg.workbench_browser_devtools_close()}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {tabs.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-muted-foreground">
          {msg.workbench_browser_devtools_no_session()}
        </div>
      ) : subTab === "console" ? (
        <div
          ref={logRef}
          data-testid="devtools-console-log"
          className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed"
        >
          {consoleMessages.length === 0 ? (
            <p className="text-muted-foreground">
              {msg.workbench_browser_devtools_console_empty()}
            </p>
          ) : (
            consoleMessages.map((m, i) => (
              <div
                key={i}
                className={`whitespace-pre-wrap break-all ${
                  m.type === "error"
                    ? "text-destructive"
                    : m.type === "warning"
                      ? "text-amber-500"
                      : "text-foreground/90"
                }`}
              >
                [{m.type}] {m.text}
              </div>
            ))
          )}
        </div>
      ) : subTab === "network" ? (
        <div
          data-testid="devtools-network-log"
          className="flex-1 overflow-y-auto px-1 py-1 text-[11px]"
        >
          {networkRequests.length === 0 ? (
            <p className="px-2 py-1 text-muted-foreground">
              {msg.workbench_browser_devtools_network_empty()}
            </p>
          ) : (
            <table className="w-full border-collapse">
              <tbody>
                {networkRequests.map((r) => (
                  <tr key={r.request_id} className="border-b border-border/30">
                    <td className="px-2 py-1 font-mono text-muted-foreground">
                      {r.method}
                    </td>
                    <td
                      className="px-2 py-1 truncate max-w-[280px]"
                      title={r.url}
                    >
                      {r.url}
                    </td>
                    <td className="px-2 py-1 text-muted-foreground">
                      {r.resource_type}
                    </td>
                    <td
                      className={`px-2 py-1 font-mono ${
                        r.status === "failed" ||
                        (typeof r.status === "number" && r.status >= 400)
                          ? "text-destructive"
                          : "text-muted-foreground"
                      }`}
                    >
                      {r.status ?? "…"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
          <div className="flex gap-1">
            <Input
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder={msg.workbench_browser_devtools_elements_placeholder()}
              className="h-7 flex-1 font-mono text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter") runScript();
              }}
            />
            <Button
              size="sm"
              onClick={runScript}
              disabled={evalLoading || !script.trim()}
              className="h-7 gap-1 text-xs"
            >
              <Play className="h-3 w-3" />
              {msg.workbench_browser_devtools_elements_run()}
            </Button>
          </div>
          {evalError ? (
            <pre className="whitespace-pre-wrap break-all rounded bg-destructive/10 p-2 text-[11px] text-destructive">
              {evalError}
            </pre>
          ) : evalResult !== null ? (
            <pre
              data-testid="devtools-eval-result"
              className="whitespace-pre-wrap break-all rounded bg-card/60 p-2 font-mono text-[11px]"
            >
              {evalResult}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground">
              {msg.workbench_browser_devtools_elements_empty()}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

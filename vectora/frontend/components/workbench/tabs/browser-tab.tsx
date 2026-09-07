"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Code2,
  ExternalLink,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  Square,
  Terminal,
  X,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useChatInputStore } from "@/lib/stores/chat-input-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { m as msg } from "@/lib/paraglide/messages";
import { BrowserDevtoolsPanel } from "./browser-devtools-panel";
import {
  getBrowserSessionGeneration,
  getBrowserSession,
  setBrowserSession,
} from "@/lib/browser-session-store";

export { clearBrowserSessionCache } from "@/lib/browser-session-store";

interface LaunchConfig {
  name: string;
  runtimeExecutable: string;
  runtimeArgs: string[];
  port: number;
  env?: Record<string, string>;
}

interface ServerStatus {
  name: string;
  port: number;
  running: boolean;
  pid?: number | null;
}

interface BrowserTabProps {
  threadId: string;
  visible?: boolean;
}

/** Esqueleto do .vectora/launch.json enviado ao agente. */
const LAUNCH_JSON_TEMPLATE = `\`\`\`json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "<server-name>",
      "runtimeExecutable": "<command>",
      "runtimeArgs": ["<args>"],
      "port": <port>
    }
  ]
}
\`\`\``;

function genId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `tab-${Math.random().toString(36).slice(2)}`;
}

function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

interface TabState {
  id: string;
  title: string;
  // caminho web (iframe) — histórico próprio, cross-origin não expõe API de
  // histórico do navegador nativo pra fora.
  history: string[];
  historyIndex: number;
  iframeKey: number;
  // caminho desktop (WebContentsView) — fonte de verdade é o Chromium
  // (eventos via bridge), nunca escrito à mão fora do listener de eventos.
  viewId: number | null;
  desktopUrl: string;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
  loadError: string | null;
}

function makeTab(id: string): TabState {
  return {
    id,
    title: "",
    history: [],
    historyIndex: -1,
    iframeKey: 0,
    viewId: null,
    desktopUrl: "",
    canGoBack: false,
    canGoForward: false,
    loading: false,
    loadError: null,
  };
}

export function BrowserTab({ threadId, visible = true }: BrowserTabProps) {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";
  const wsIdRef = useRef(wsId);
  wsIdRef.current = wsId;
  const sessionKey = `${wsId}:${threadId}`;
  const settingsOpen = useSettingsOverlayStore((s) => s.open);

  // Presente só no desktop Electron — quando ausente, cai no `<iframe>` de
  // fallback abaixo (sujeito a X-Frame-Options, único caminho possível fora
  // do Electron). Ver electron/src/browser-view-manager.ts.
  const desktopBrowser =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;

  const [configs, setConfigs] = useState<LaunchConfig[]>([]);
  const [statuses, setStatuses] = useState<ServerStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showManualForm, setShowManualForm] = useState(false);
  const [serversCollapsed, setServersCollapsed] = useState(false);
  const [manual, setManual] = useState({
    name: "",
    runtimeExecutable: "",
    runtimeArgs: "",
    port: "",
  });
  const [consoleFor, setConsoleFor] = useState<string | null>(null);
  const [consoleLines, setConsoleLines] = useState<string[]>([]);
  const consoleSelectionRef = useRef(0);
  const consoleRequestRef = useRef(0);
  const workspaceGenerationRef = useRef(0);
  const actionGenerationRef = useRef(0);
  const statusRequestRef = useRef(0);
  const saveQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const [consoleLoading, setConsoleLoading] = useState(false);
  // Painel de devtools da sessão do AGENTE (Playwright headless) — distinto
  // do console de stdout do dev server acima, que é sobre o processo, não
  // sobre a página que o agente navega via tools de browser.
  const [devtoolsOpen, setDevtoolsOpen] = useState(false);

  // Múltiplas abas — cada uma com seu próprio histórico (web) ou sua
  // própria WebContentsView (desktop, cada uma com viewId próprio; o
  // BrowserViewManager do main process já suporta N views por design, ver
  // electron/src/browser-view-manager.ts — esta camada é só a UI de gestão).
  const [tabs, setTabs] = useState<TabState[]>(() => {
    const restored = getBrowserSession(sessionKey);
    return restored
      ? restored.tabs.map((tab) => ({
          ...tab,
          loading: false,
          loadError: null,
        }))
      : [makeTab(genId())];
  });
  const [activeTabId, setActiveTabId] = useState<string>(() => {
    const restored = getBrowserSession(sessionKey);
    return restored?.activeTabId ?? tabs[0].id;
  });
  const tabsRef = useRef(tabs);
  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];

  useEffect(() => {
    setBrowserSession(sessionKey, {
      activeTabId,
      tabs: tabs.map(({ loading, loadError, ...tab }) => tab),
    });
  }, [sessionKey, tabs, activeTabId]);

  const [urlInput, setUrlInput] = useState("");
  const [editingUrl, setEditingUrl] = useState(false);

  const pendingNavigateRef = useRef<Map<string, string>>(new Map());
  const pendingViewCreatesRef = useRef<Set<string>>(new Set());
  const browserViewContainerRef = useRef<HTMLDivElement>(null);

  const iframeRef = useRef<HTMLIFrameElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consolePollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consoleLogRef = useRef<HTMLDivElement>(null);
  // Rastreia running por nome entre polls — só dispara auto-navegação na
  // transição false→true, nunca no primeiro poll (senão rouba o foco da
  // URL atual toda vez que a aba monta com um servidor já rodando).
  const prevRunningRef = useRef<Record<string, boolean> | null>(null);

  const currentUrl = desktopBrowser
    ? activeTab.desktopUrl
    : activeTab.historyIndex >= 0
      ? activeTab.history[activeTab.historyIndex]
      : "";
  const canGoBack = desktopBrowser
    ? activeTab.canGoBack
    : activeTab.historyIndex > 0;
  const canGoForward = desktopBrowser
    ? activeTab.canGoForward
    : activeTab.historyIndex < activeTab.history.length - 1;
  const desktopLoading = activeTab.loading;
  const desktopLoadError = activeTab.loadError;

  const updateTab = useCallback(
    (id: string, patch: Partial<TabState> | ((t: TabState) => TabState)) => {
      setTabs((prev) =>
        prev.map((t) =>
          t.id === id
            ? typeof patch === "function"
              ? patch(t)
              : { ...t, ...patch }
            : t,
        ),
      );
    },
    [],
  );

  const navigateInTab = useCallback(
    (tabId: string, raw: string) => {
      const url = normalizeUrl(raw);
      if (!url) return;
      if (desktopBrowser) {
        updateTab(tabId, { desktopUrl: url });
        const tab = tabsRef.current.find((t) => t.id === tabId);
        if (!tab || tab.viewId === null) {
          // View ainda não terminou de nascer (createView é async) — fica
          // pendente e é disparada assim que o id chegar.
          pendingNavigateRef.current.set(tabId, url);
          return;
        }
        void desktopBrowser.navigate(tab.viewId, url).then((result) => {
          if (!result.ok) updateTab(tabId, { loadError: result.error ?? null });
        });
        return;
      }
      updateTab(tabId, (t) => {
        const base = t.history.slice(0, t.historyIndex + 1);
        const next = [...base, url];
        return {
          ...t,
          history: next,
          historyIndex: next.length - 1,
          iframeKey: t.iframeKey + 1,
        };
      });
    },
    [desktopBrowser, updateTab],
  );

  const navigate = useCallback(
    (raw: string) => navigateInTab(activeTabId, raw),
    [navigateInTab, activeTabId],
  );

  const createDesktopView = useCallback(
    (tabId: string) => {
      if (!desktopBrowser) return;
      const sessionGeneration = getBrowserSessionGeneration(sessionKey);
      pendingViewCreatesRef.current.add(tabId);
      void desktopBrowser.createView().then((viewId) => {
        if (
          !pendingViewCreatesRef.current.has(tabId) ||
          getBrowserSessionGeneration(sessionKey) !== sessionGeneration
        ) {
          desktopBrowser.destroyView(viewId);
          return;
        }
        pendingViewCreatesRef.current.delete(tabId);
        if (!tabsRef.current.some((tab) => tab.id === tabId)) {
          pendingNavigateRef.current.delete(tabId);
          desktopBrowser.destroyView(viewId);
          return;
        }
        const pending = pendingNavigateRef.current.get(tabId);
        updateTab(tabId, {
          viewId,
          ...(pending ? { desktopUrl: pending } : {}),
        });
        if (pending) {
          pendingNavigateRef.current.delete(tabId);
          void desktopBrowser.navigate(viewId, pending).then((result) => {
            if (!result.ok)
              updateTab(tabId, { loadError: result.error ?? null });
          });
        }
      });
    },
    [desktopBrowser, sessionKey, updateTab],
  );

  const addTab = useCallback(
    (url?: string) => {
      const id = genId();
      setTabs((prev) => [...prev, makeTab(id)]);
      setActiveTabId(id);
      if (url) {
        if (desktopBrowser) {
          pendingNavigateRef.current.set(id, normalizeUrl(url));
        } else {
          const normalized = normalizeUrl(url);
          setTabs((prev) =>
            prev.map((t) =>
              t.id === id
                ? { ...t, history: [normalized], historyIndex: 0 }
                : t,
            ),
          );
        }
      }
      if (desktopBrowser) {
        pendingViewCreatesRef.current.add(id);
        createDesktopView(id);
      }
      return id;
    },
    [desktopBrowser, createDesktopView],
  );

  const closeTab = useCallback(
    (id: string) => {
      const closing = tabsRef.current.find((t) => t.id === id);
      pendingViewCreatesRef.current.delete(id);
      pendingNavigateRef.current.delete(id);
      if (desktopBrowser && closing?.viewId != null) {
        desktopBrowser.destroyView(closing.viewId);
      }
      const remaining = tabsRef.current.filter((t) => t.id !== id);
      if (remaining.length === 0) {
        const freshId = genId();
        setTabs([makeTab(freshId)]);
        setActiveTabId(freshId);
        if (desktopBrowser) createDesktopView(freshId);
        return;
      }
      setTabs(remaining);
      if (id === activeTabId) {
        const idx = tabsRef.current.findIndex((t) => t.id === id);
        const neighbor = remaining[Math.min(idx, remaining.length - 1)];
        setActiveTabId(neighbor.id);
      }
    },
    [desktopBrowser, activeTabId, createDesktopView],
  );

  const goBack = useCallback(() => {
    if (!canGoBack) return;
    if (desktopBrowser && activeTab.viewId !== null) {
      desktopBrowser.goBack(activeTab.viewId);
      return;
    }
    updateTab(activeTabId, (t) => ({
      ...t,
      historyIndex: t.historyIndex - 1,
      iframeKey: t.iframeKey + 1,
    }));
  }, [canGoBack, desktopBrowser, activeTab.viewId, activeTabId, updateTab]);

  const goForward = useCallback(() => {
    if (!canGoForward) return;
    if (desktopBrowser && activeTab.viewId !== null) {
      desktopBrowser.goForward(activeTab.viewId);
      return;
    }
    updateTab(activeTabId, (t) => ({
      ...t,
      historyIndex: t.historyIndex + 1,
      iframeKey: t.iframeKey + 1,
    }));
  }, [canGoForward, desktopBrowser, activeTab.viewId, activeTabId, updateTab]);

  const refresh = useCallback(() => {
    if (desktopBrowser && activeTab.viewId !== null) {
      desktopBrowser.reload(activeTab.viewId);
      return;
    }
    updateTab(activeTabId, (t) => ({ ...t, iframeKey: t.iframeKey + 1 }));
  }, [desktopBrowser, activeTab.viewId, activeTabId, updateTab]);

  // Cria uma WebContentsView para cada aba restaurada. A troca de workbench
  // apenas torna as views invisíveis; fechar uma aba continua sendo a ação
  // destrutiva explícita.
  const hideAllBrowserViews = useCallback(() => {
    if (!desktopBrowser) return;
    for (const tab of tabsRef.current) {
      if (tab.viewId !== null) desktopBrowser.setVisible(tab.viewId, false);
    }
  }, [desktopBrowser]);

  useEffect(() => {
    if (!desktopBrowser) return;
    let cancelled = false;
    for (const tab of tabsRef.current.filter(
      (candidate) => candidate.viewId === null,
    )) {
      pendingViewCreatesRef.current.add(tab.id);
      const sessionGeneration = getBrowserSessionGeneration(sessionKey);
      void desktopBrowser.createView().then((viewId) => {
        if (
          !pendingViewCreatesRef.current.has(tab.id) ||
          getBrowserSessionGeneration(sessionKey) !== sessionGeneration
        ) {
          desktopBrowser.destroyView(viewId);
          return;
        }
        pendingViewCreatesRef.current.delete(tab.id);
        if (cancelled) {
          desktopBrowser.destroyView(viewId);
          return;
        }
        if (!tabsRef.current.some((candidate) => candidate.id === tab.id)) {
          pendingNavigateRef.current.delete(tab.id);
          desktopBrowser.destroyView(viewId);
          return;
        }
        setTabs((prev) =>
          prev.map((candidate) =>
            candidate.id === tab.id ? { ...candidate, viewId } : candidate,
          ),
        );
        if (tab.desktopUrl)
          void desktopBrowser.navigate(viewId, tab.desktopUrl);
      });
    }
    return () => {
      cancelled = true;
      hideAllBrowserViews();
    };
  }, [desktopBrowser, hideAllBrowserViews, sessionKey]);

  useEffect(() => {
    const pendingNavigate = pendingNavigateRef.current;
    const pendingViewCreates = pendingViewCreatesRef.current;
    return () => {
      // The session is intentionally retained while the workbench is hidden;
      // deletion flows call disposeBrowserSession explicitly.
      pendingNavigate.clear();
      pendingViewCreates.clear();
    };
  }, []);

  // Espelha os eventos de navegação nativos do Chromium (fonte de verdade)
  // pro estado React, roteando por viewId pra achar a aba certa (pode ser
  // uma aba em segundo plano, não necessariamente a ativa) — nunca escreve
  // em desktopUrl/canGoBack/canGoForward fora daqui, exceto a atualização
  // otimista de `desktopUrl` em navigateInTab() (feedback imediato na barra
  // antes do evento `navigated` real chegar).
  useEffect(() => {
    if (!desktopBrowser) return;
    return desktopBrowser.onEvent((eventViewId, event) => {
      setTabs((prev) =>
        prev.map((t) => {
          if (t.viewId !== eventViewId) return t;
          if (event.type === "navigated") {
            return {
              ...t,
              desktopUrl: event.url ?? t.desktopUrl,
              canGoBack: event.canGoBack ?? t.canGoBack,
              canGoForward: event.canGoForward ?? t.canGoForward,
              loadError: null,
            };
          }
          if (event.type === "loadingChanged") {
            return { ...t, loading: event.isLoading ?? t.loading };
          }
          if (event.type === "loadFailed") {
            return { ...t, loadError: event.errorDescription ?? null };
          }
          if (event.type === "titleUpdated" && event.title) {
            return { ...t, title: event.title };
          }
          return t;
        }),
      );
    });
  }, [desktopBrowser]);

  // Visibilidade: só a view da aba ATIVA fica visível — todas as outras
  // (abas em segundo plano) ficam escondidas, senão desenhariam por cima
  // umas das outras (WebContentsView é sempre desenhada por cima do DOM).
  useEffect(() => {
    if (!desktopBrowser) return;
    for (const t of tabs) {
      if (t.viewId !== null) {
        desktopBrowser.setVisible(
          t.viewId,
          visible && !settingsOpen && t.id === activeTabId,
        );
      }
    }
  }, [desktopBrowser, tabs, activeTabId, settingsOpen, visible]);

  // Reporta os bounds reais do container (ResizeObserver) pro main process
  // posicionar a WebContentsView ATIVA por cima — só existe depois da
  // primeira navegação daquela aba (currentUrl truthy é quando o container
  // abaixo é renderizado). Depende de `hasUrl`/viewId da aba ativa, não da
  // URL em si — o container é o mesmo nó DOM entre navegações subsequentes.
  const hasUrl = Boolean(currentUrl);
  const activeViewId = activeTab.viewId;
  useEffect(() => {
    if (!desktopBrowser || activeViewId === null || !hasUrl) return;
    const el = browserViewContainerRef.current;
    if (!el) return;
    const report = () => {
      const rect = el.getBoundingClientRect();
      desktopBrowser.setBounds(activeViewId, {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      });
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    window.addEventListener("resize", report);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", report);
    };
  }, [desktopBrowser, activeViewId, hasUrl]);

  const fetchLaunch = useCallback(
    async (isCurrent: () => boolean = () => true) => {
      if (!wsId) return;
      try {
        const res = await fetch(
          `/workspaces/${encodeURIComponent(wsId)}/browser/launch`,
        );
        if (res.ok) {
          const data = (await res.json()) as { configurations: LaunchConfig[] };
          if (isCurrent()) setConfigs(data.configurations ?? []);
        }
      } catch {
        // silently ignore
      } finally {
        if (isCurrent()) setIsLoading(false);
      }
    },
    [wsId],
  );

  const fetchStatus = useCallback(
    async (isCurrent: () => boolean): Promise<ServerStatus[] | null> => {
      if (!wsId) return null;
      const requestId = ++statusRequestRef.current;
      const isLatest = () =>
        isCurrent() && statusRequestRef.current === requestId;
      try {
        const res = await fetch(
          `/workspaces/${encodeURIComponent(wsId)}/browser/status`,
        );
        if (res.ok) {
          const data = (await res.json()) as { servers: ServerStatus[] };
          const servers = data.servers ?? [];
          if (!isLatest()) return null;
          setStatuses(servers);

          // Auto-navegação: qualquer servidor que passe de parado pra rodando
          // entre um poll e outro abre sozinho numa aba NOVA — funciona tanto
          // pro clique manual (handleStart) quanto pra tool `browser_start` do
          // agente, que sobe o servidor sem passar por nenhum handler do UI.
          // Aba nova (não substitui a ativa) preserva a navegação livre que o
          // usuário já tinha aberto.
          const prevRunning = prevRunningRef.current;
          const nextRunning: Record<string, boolean> = {};
          for (const server of servers) {
            nextRunning[server.name] = server.running;
            if (prevRunning && server.running && !prevRunning[server.name]) {
              addTab(`http://localhost:${server.port}`);
            }
          }
          prevRunningRef.current = nextRunning;

          return servers;
        }
      } catch {
        // silently ignore
      }
      return null;
    },
    [wsId, addTab],
  );

  const fetchConsoleLogs = useCallback(
    async (name: string, isCurrent: () => boolean) => {
      if (!wsId) return;
      const requestId = ++consoleRequestRef.current;
      const isLatest = () =>
        isCurrent() && consoleRequestRef.current === requestId;
      if (isLatest()) setConsoleLoading(true);
      try {
        const res = await fetch(
          `/workspaces/${encodeURIComponent(wsId)}/browser/logs?name=${encodeURIComponent(name)}`,
        );
        if (res.ok) {
          const data = (await res.json()) as { lines: string[] };
          if (isLatest()) setConsoleLines(data.lines ?? []);
        }
      } catch {
        // silently ignore — o painel continua com as últimas linhas conhecidas
      } finally {
        if (isLatest()) setConsoleLoading(false);
      }
    },
    [wsId],
  );

  useEffect(() => {
    let alive = true;
    const selection = ++consoleSelectionRef.current;
    setConsoleLines([]);
    if (!consoleFor) {
      if (consolePollRef.current) clearInterval(consolePollRef.current);
      return () => {
        alive = false;
      };
    }
    const load = () =>
      fetchConsoleLogs(
        consoleFor,
        () => alive && consoleSelectionRef.current === selection,
      );
    void Promise.resolve().then(load);
    consolePollRef.current = setInterval(load, 3000);
    return () => {
      alive = false;
      if (consolePollRef.current) clearInterval(consolePollRef.current);
    };
  }, [consoleFor, fetchConsoleLogs]);

  useEffect(() => {
    const el = consoleLogRef.current;
    if (el && consoleLines.length > 0) el.scrollTop = el.scrollHeight;
  }, [consoleLines]);

  useEffect(() => {
    let alive = true;
    const generation = ++workspaceGenerationRef.current;
    void Promise.resolve().then(() =>
      fetchLaunch(() => alive && workspaceGenerationRef.current === generation),
    );
    return () => {
      alive = false;
      if (workspaceGenerationRef.current === generation) {
        workspaceGenerationRef.current += 1;
      }
    };
  }, [fetchLaunch]);

  useEffect(() => {
    let alive = true;
    if (!wsId || configs.length === 0) return;
    const load = () => fetchStatus(() => alive && wsIdRef.current === wsId);
    void Promise.resolve().then(load);
    pollRef.current = setInterval(load, 3000);
    return () => {
      alive = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [wsId, configs.length, fetchStatus]);

  const saveConfigs = useCallback(
    async (next: LaunchConfig[]) => {
      const expectedWsId = wsId;
      const generation = workspaceGenerationRef.current;
      const save = saveQueueRef.current.then(async () => {
        const saveRes = await fetch(
          `/workspaces/${encodeURIComponent(expectedWsId)}/browser/launch`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version: "0.0.1", configurations: next }),
          },
        );
        const current =
          wsIdRef.current === expectedWsId &&
          workspaceGenerationRef.current === generation;
        if (saveRes.ok && current) {
          setConfigs(next);
          void fetchStatus(
            () =>
              wsIdRef.current === expectedWsId &&
              workspaceGenerationRef.current === generation,
          );
        }
        return saveRes.ok && current;
      });
      saveQueueRef.current = save.catch(() => undefined);
      return save;
    },
    [wsId, fetchStatus],
  );

  const handleAskAgent = useCallback(() => {
    const prompt = [
      msg.workbench_browser_ask_agent_prompt(),
      LAUNCH_JSON_TEMPLATE,
      msg.workbench_browser_ask_agent_note(),
    ].join("\n\n");
    useChatInputStore.getState().pushDraft(prompt);
  }, []);

  const handleManualSave = async () => {
    if (!wsId || !manual.name.trim() || !manual.runtimeExecutable.trim())
      return;
    const port = Number.parseInt(manual.port, 10);
    const cfg: LaunchConfig = {
      name: manual.name.trim(),
      runtimeExecutable: manual.runtimeExecutable.trim(),
      runtimeArgs: manual.runtimeArgs.trim()
        ? manual.runtimeArgs.trim().split(/\s+/)
        : [],
      port: Number.isFinite(port) ? port : 3000,
    };
    const ok = await saveConfigs([
      ...configs.filter((c) => c.name !== cfg.name),
      cfg,
    ]);
    if (ok) {
      setManual({ name: "", runtimeExecutable: "", runtimeArgs: "", port: "" });
      setShowManualForm(false);
    }
  };

  const handleStart = async (cfg: LaunchConfig) => {
    if (!wsId) return;
    const expectedWsId = wsId;
    const generation = workspaceGenerationRef.current;
    const operation = ++actionGenerationRef.current;
    setActionLoading(cfg.name);
    try {
      // POST bloqueia no backend até a porta abrir (ou ~15s de timeout) —
      // não precisa de espera fixa aqui; o status refletido já é real.
      // A navegação automática acontece dentro de fetchStatus() (transição
      // false→true), o mesmo caminho usado quando o servidor sobe via tool
      // do agente — não duplica lógica aqui.
      await fetch(`/workspaces/${encodeURIComponent(wsId)}/browser/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: cfg.name }),
      });
      await fetchStatus(
        () =>
          wsIdRef.current === expectedWsId &&
          workspaceGenerationRef.current === generation &&
          actionGenerationRef.current === operation,
      );
    } finally {
      if (
        wsIdRef.current === expectedWsId &&
        workspaceGenerationRef.current === generation &&
        actionGenerationRef.current === operation
      ) {
        setActionLoading(null);
      }
    }
  };

  const handleStop = async (name: string) => {
    if (!wsId) return;
    const expectedWsId = wsId;
    const generation = workspaceGenerationRef.current;
    const operation = ++actionGenerationRef.current;
    setActionLoading(name);
    try {
      await fetch(`/workspaces/${encodeURIComponent(wsId)}/browser/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      await fetchStatus(
        () =>
          wsIdRef.current === expectedWsId &&
          workspaceGenerationRef.current === generation &&
          actionGenerationRef.current === operation,
      );
    } finally {
      if (
        wsIdRef.current === expectedWsId &&
        workspaceGenerationRef.current === generation &&
        actionGenerationRef.current === operation
      ) {
        setActionLoading(null);
      }
    }
  };

  const getStatus = (name: string): ServerStatus | undefined =>
    statuses.find((s) => s.name === name);

  // `allow-same-origin` junto de `allow-scripts` é a combinação clássica de
  // sandbox-escape (o conteúdo do iframe passa a poder acessar/manipular o
  // document do pai) — só é seguro pra um servidor de dev do próprio
  // workspace (processo local do usuário, mesma confiança que terminal/PTY
  // já assumem), nunca pra navegação livre a um site externo qualquer.
  // Sem esse flag, o iframe recebe origem opaca mesmo pra localhost, o que
  // faz o Next.js (allowedDevOrigins) bloquear os assets — daí o CSS sumir.
  const isTrustedWorkspaceServer = (url: string): boolean => {
    try {
      const parsed = new URL(url);
      if (parsed.hostname !== "localhost" && parsed.hostname !== "127.0.0.1")
        return false;
      const port = parsed.port
        ? Number.parseInt(parsed.port, 10)
        : parsed.protocol === "https:"
          ? 443
          : 80;
      return configs.some((c) => c.port === port);
    } catch {
      return false;
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const manualForm = (
    <div className="w-full max-w-[260px] space-y-1.5 rounded-md border border-border/60 bg-card/40 p-2 text-left">
      <Input
        value={manual.name}
        onChange={(e) => setManual((m) => ({ ...m, name: e.target.value }))}
        placeholder={msg.workbench_browser_field_name()}
        className="h-7 text-xs"
      />
      <Input
        value={manual.runtimeExecutable}
        onChange={(e) =>
          setManual((m) => ({ ...m, runtimeExecutable: e.target.value }))
        }
        placeholder={msg.workbench_browser_field_executable()}
        className="h-7 text-xs"
      />
      <Input
        value={manual.runtimeArgs}
        onChange={(e) =>
          setManual((m) => ({ ...m, runtimeArgs: e.target.value }))
        }
        placeholder={msg.workbench_browser_field_args()}
        className="h-7 text-xs"
      />
      <Input
        value={manual.port}
        onChange={(e) => setManual((m) => ({ ...m, port: e.target.value }))}
        placeholder={msg.workbench_browser_field_port()}
        inputMode="numeric"
        className="h-7 text-xs"
      />
      <div className="flex gap-1 pt-0.5">
        <Button
          size="sm"
          onClick={handleManualSave}
          disabled={!manual.name.trim() || !manual.runtimeExecutable.trim()}
          className="h-7 flex-1 text-xs"
        >
          {msg.workbench_browser_manual_save()}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setShowManualForm(false)}
          className="h-7 text-xs"
        >
          {msg.workbench_browser_manual_cancel()}
        </Button>
      </div>
    </div>
  );

  const serverFavorites = configs.length > 0 && (
    <div className="shrink-0 border-b border-border/60 bg-card/40 px-3 py-2 space-y-1.5">
      <div className="flex w-full items-center justify-between">
        <button
          className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setServersCollapsed((v) => !v)}
          title={
            serversCollapsed
              ? msg.workbench_browser_servers_expand()
              : msg.workbench_browser_servers_collapse()
          }
        >
          {serversCollapsed ? (
            <ChevronRight className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )}
          {msg.workbench_browser_servers()}
        </button>
        <button
          onClick={() => setShowManualForm((v) => !v)}
          className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          title={msg.workbench_browser_manual_add()}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      {!serversCollapsed && showManualForm && (
        <div className="pb-1">{manualForm}</div>
      )}
      {!serversCollapsed &&
        configs.map((cfg) => {
          const status = getStatus(cfg.name);
          const isRunning = status?.running ?? false;
          // `running` reflete só a porta estar aberta (qualquer processo,
          // de qualquer config) — `pid` só existe quando ESTE config foi
          // quem de fato subiu o processo rastreado pelo Vectora. Sem essa
          // distinção, dois configs que colidem na mesma porta (ex.: "API
          // (Local)" e o serviço web de "Full Stack (Turbo)" configurados
          // pro mesmo 3333) mostravam os dois como "rodando", com um botão
          // de parar que não fazia nada (nenhum processo rastreado sob
          // aquele nome pra matar).
          const isManaged = status?.pid != null;
          const isAction = actionLoading === cfg.name;
          const isActive = currentUrl === `http://localhost:${cfg.port}`;

          return (
            <div
              key={cfg.name}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent/40 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 rounded-full shrink-0 ${isRunning ? "bg-green-500" : "bg-muted-foreground/40"}`}
                  />
                  <span className="text-xs font-medium truncate">
                    {cfg.name}
                  </span>
                  {isRunning && (
                    <span className="text-[10px] text-muted-foreground">
                      :{cfg.port}
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-muted-foreground/60 truncate pl-3">
                  {cfg.runtimeExecutable} {cfg.runtimeArgs.join(" ")}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6"
                  title={msg.workbench_browser_console()}
                  onClick={() => setConsoleFor(cfg.name)}
                >
                  <Terminal className="h-3 w-3" />
                </Button>
                {isRunning && (
                  <Button
                    size="icon"
                    variant={isActive ? "secondary" : "ghost"}
                    className="h-6 w-6"
                    title={msg.workbench_browser_open_server()}
                    onClick={() => navigate(`http://localhost:${cfg.port}`)}
                  >
                    <ExternalLink className="h-3 w-3" />
                  </Button>
                )}
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6"
                  disabled={isAction || (isRunning && !isManaged)}
                  onClick={() =>
                    isRunning ? handleStop(cfg.name) : handleStart(cfg)
                  }
                  title={
                    isRunning
                      ? isManaged
                        ? msg.workbench_browser_stop()
                        : msg.workbench_browser_running_external()
                      : msg.workbench_browser_start()
                  }
                >
                  {isAction ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : isRunning ? (
                    <Square className="h-3 w-3 fill-current" />
                  ) : (
                    <Play className="h-3 w-3 fill-current" />
                  )}
                </Button>
              </div>
            </div>
          );
        })}
    </div>
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {serverFavorites}

      {/* Tab strip — favicon/título truncado + fechar por aba, "+" pra nova
          aba. Sempre visível (mesmo com 1 aba só), consistente com um
          browser real. */}
      <div className="flex shrink-0 items-center gap-0.5 overflow-x-auto border-b border-border/60 bg-card/10 px-1 pt-1">
        {tabs.map((t) => {
          const tUrl = desktopBrowser
            ? t.desktopUrl
            : t.historyIndex >= 0
              ? t.history[t.historyIndex]
              : "";
          const label = t.title || tUrl || msg.workbench_browser_tab_untitled();
          const isTabActive = t.id === activeTabId;
          return (
            <div
              key={t.id}
              data-testid="browser-tab-strip-item"
              onClick={() => setActiveTabId(t.id)}
              className={`group flex max-w-[160px] shrink-0 items-center gap-1 rounded-t-md px-2 py-1 text-[11px] cursor-pointer transition-colors ${
                isTabActive
                  ? "bg-background text-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              <span className="min-w-0 flex-1 truncate">{label}</span>
              <button
                type="button"
                className="shrink-0 rounded p-0.5 opacity-0 group-hover:opacity-100 hover:bg-accent transition-opacity"
                title={msg.workbench_browser_close_tab()}
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(t.id);
                }}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </div>
          );
        })}
        <button
          type="button"
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          title={msg.workbench_browser_new_tab()}
          onClick={() => addTab()}
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>

      {/* Barra de navegação — sempre ativa, não depende de nenhum servidor */}
      <div className="flex items-center gap-1 border-b border-border/60 bg-card/20 px-2 py-1">
        <button
          className="rounded p-1 hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
          onClick={goBack}
          disabled={!canGoBack}
          title={msg.workbench_browser_back()}
        >
          <ArrowLeft className="h-3 w-3" />
        </button>
        <button
          className="rounded p-1 hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
          onClick={goForward}
          disabled={!canGoForward}
          title={msg.workbench_browser_forward()}
        >
          <ArrowRight className="h-3 w-3" />
        </button>
        <button
          className="rounded p-1 hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
          onClick={refresh}
          disabled={!currentUrl}
          title={msg.workbench_files_refresh()}
        >
          {desktopLoading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
        </button>
        <input
          type="text"
          data-testid="browser-url-bar"
          className="flex-1 min-w-0 bg-background/80 border border-border/40 rounded px-2 py-0.5 text-[11px] font-mono text-foreground focus:outline-none focus:border-primary/60"
          value={editingUrl ? urlInput : currentUrl}
          placeholder={msg.workbench_browser_url_placeholder()}
          onFocus={() => {
            setEditingUrl(true);
            setUrlInput(currentUrl);
          }}
          onBlur={() => setEditingUrl(false)}
          onChange={(e) => setUrlInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              navigate(urlInput);
              (e.target as HTMLInputElement).blur();
            }
          }}
        />
        <a
          href={currentUrl || undefined}
          target="_blank"
          rel="noreferrer"
          aria-disabled={!currentUrl}
          className={`rounded p-1 hover:bg-accent text-muted-foreground hover:text-foreground transition-colors ${!currentUrl ? "opacity-30 pointer-events-none" : ""}`}
          title={msg.workbench_browser_open_external()}
        >
          <ExternalLink className="h-3 w-3" />
        </a>
        <button
          data-testid="browser-devtools-toggle"
          className={`rounded p-1 hover:bg-accent transition-colors ${devtoolsOpen ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          onClick={() => setDevtoolsOpen((v) => !v)}
          title={msg.workbench_browser_devtools_toggle()}
        >
          <Code2 className="h-3 w-3" />
        </button>
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        {desktopLoadError && (
          <div className="shrink-0 border-b border-destructive/40 bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
            {desktopLoadError}
          </div>
        )}
        {currentUrl ? (
          desktopBrowser ? (
            // WebContentsView real (Electron) desenhada pelo main process por
            // cima deste espaço reservado — este div fica sempre vazio, é só
            // a referência de bounds (ver o efeito de ResizeObserver acima).
            <div
              ref={browserViewContainerRef}
              data-testid="browser-webcontentsview-container"
              className="flex-1 w-full bg-white"
            />
          ) : (
            <iframe
              ref={iframeRef}
              key={`${activeTab.id}-${activeTab.iframeKey}`}
              src={currentUrl}
              className="flex-1 w-full border-0 bg-white"
              title="Browser"
              sandbox={
                isTrustedWorkspaceServer(currentUrl)
                  ? "allow-scripts allow-forms allow-modals allow-popups allow-same-origin"
                  : "allow-scripts allow-forms allow-modals allow-popups"
              }
            />
          )
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center pb-[18%] gap-3 p-4 text-center">
            <Zap className="h-8 w-8 text-muted-foreground/40 shrink-0" />
            <div className="min-w-0 max-w-[260px]">
              <p className="text-sm font-medium text-foreground leading-snug">
                {msg.workbench_browser_empty_title()}
              </p>
              <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
                {msg.workbench_browser_empty_description()}
              </p>
            </div>
            {configs.length === 0 &&
              (showManualForm ? (
                manualForm
              ) : (
                <div className="flex w-full max-w-[220px] flex-col gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={handleAskAgent}
                    className="gap-1.5 w-full h-auto py-1.5 px-3"
                  >
                    <Sparkles className="h-3.5 w-3.5 shrink-0" />
                    <span className="text-xs">
                      {msg.workbench_browser_ask_agent()}
                    </span>
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowManualForm(true)}
                    className="gap-1.5 w-full h-auto py-1.5 px-3"
                  >
                    <Plus className="h-3.5 w-3.5 shrink-0" />
                    <span className="text-xs">
                      {msg.workbench_browser_manual_add()}
                    </span>
                  </Button>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Painel de console inline — dentro do fluxo da própria aba, não um
          portal/dialog (senão sobrepõe a janela inteira em vez de dividir
          o espaço com o iframe acima). */}
      {consoleFor !== null && (
        <div className="flex h-56 shrink-0 flex-col border-t border-border/60 bg-background">
          <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-2">
            <span className="text-xs font-medium text-foreground">
              {msg.workbench_browser_console_title({ name: consoleFor })}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => {
                  if (!consoleFor) return;
                  const selection = consoleSelectionRef.current;
                  void fetchConsoleLogs(
                    consoleFor,
                    () => consoleSelectionRef.current === selection,
                  );
                }}
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                title={msg.workbench_browser_console_refresh()}
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${consoleLoading ? "animate-spin" : ""}`}
                />
              </button>
              <button
                onClick={() => setConsoleFor(null)}
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                title={msg.workbench_browser_console_close()}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <div
            ref={consoleLogRef}
            className="flex-1 overflow-y-auto bg-background px-4 py-2 font-mono text-[11px] leading-relaxed text-foreground/90"
          >
            {consoleLines.length === 0 ? (
              <p className="text-muted-foreground">
                {msg.workbench_browser_console_empty()}
              </p>
            ) : (
              consoleLines.map((line, i) => (
                <div key={i} className="whitespace-pre-wrap break-all">
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {devtoolsOpen && wsId && (
        <BrowserDevtoolsPanel
          wsId={wsId}
          onClose={() => setDevtoolsOpen(false)}
        />
      )}
    </div>
  );
}

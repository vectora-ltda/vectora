"use client";

/**
 * FilesTab — explorador de arquivos do workspace ativo.
 *
 * Estado vive no workbench-store (slice `files`), sobrevivendo a remount:
 * árvore expandida e entradas carregadas, arquivo aberto + conteúdo, filtro
 * de busca. Arquivos fixados ("pin") persistem por threadId via `pinnedFiles`.
 *
 * Funcionalidades: toolbar (novo arquivo/pasta, refresh), criação inline na
 * árvore, delete em hover (arquivo e pasta) e botão @ que injeta o caminho
 * como @mention no chat.
 */

import {
  AppWindow,
  AtSign,
  FilePlus,
  Filter,
  FolderOpen,
  FolderPlus,
  History,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import { useToastStore } from "@/lib/stores/toast-store";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
  type DiffSummary,
} from "@/lib/stores/workbench-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { fetchFile, apiUpdateFile } from "@/lib/api/fs-files";
import { VerticalSplit } from "@/components/layout/vertical-split";
import { getMediaKind, MediaView } from "@/components/workbench/file-viewer";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { usePins } from "@/lib/hooks/use-pins";
import { m } from "@/lib/paraglide/messages";
import { WorkspaceSelector } from "@/components/sidebar/workspace-selector";
import { norm } from "./files-utils";
import {
  fetchDiffSummary,
  apiFsCreate,
  apiFsDelete,
  apiFsMove,
  apiFsSearch,
  apiFsGitLogFile,
  apiFsGitShow,
  type SearchHit,
  type SearchResult,
  type FileLogEntry,
  type ShowFileAtRevResponse,
} from "./files-api";
import { FileHistoryPanel } from "./file-history-panel";
import { SearchResultGroup } from "./search-result-group";
import { DirNode, type CreatingState } from "./dir-node";
import { PinnedSection } from "./pinned-section";

// ---------------------------------------------------------------------------
// FilesTab principal
// ---------------------------------------------------------------------------

interface FilesTabProps {
  threadId: string;
  /** Adicionar arquivo/pasta ao contexto do chat como @mention. */
  onAddToContext?: (path: string) => void;
}

export function FilesTab({ threadId, onAddToContext }: FilesTabProps) {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";

  // Pins: backend é a fonte de verdade — carrega ao abrir a sessão.
  usePins(threadId);

  const filter = useWorkbenchStore((s) => s.getFiles(wsId).filter);
  const openPath = useWorkbenchStore((s) => s.getFiles(wsId).openPath);
  const openContent = useWorkbenchStore((s) =>
    openPath ? s.getFiles(wsId).contents[openPath] : undefined,
  );
  const setFilesFilter = useWorkbenchStore((s) => s.setFilesFilter);
  const setOpenFile = useWorkbenchStore((s) => s.setOpenFile);
  const setFileContent = useWorkbenchStore((s) => s.setFileContent);
  const invalidateFiles = useWorkbenchStore((s) => s.invalidateFiles);
  const viewerHeight = useWorkbenchStore((s) => s.viewerHeight);
  const setViewerHeight = useWorkbenchStore((s) => s.setViewerHeight);
  const openWindow = useWindowsStore((s) => s.open);
  const uiMode = useSettingsStore((s) => s.uiMode);

  // aria-busy: verdadeiro enquanto a raiz ainda não chegou do servidor.
  const rootEntriesLoaded = useWorkbenchStore(
    (s) => wsId !== "" && Array.isArray(s.getFiles(wsId).entriesByDir[""]),
  );
  const showSkeleton = !rootEntriesLoaded && !!wsId;

  // Badges M/A/D na árvore: join client-side com o diff porcelain.
  const diffSummary = useWorkbenchStore((s) => s.getGit(wsId).summary);
  const diffFetchedAt = useWorkbenchStore(
    (s) => s.getGit(wsId).summaryFetchedAt,
  );
  const setGitSummary = useWorkbenchStore((s) => s.setGitSummary);
  const clearPending = useWorkbenchStore((s) => s.clearPending);

  // Abrir/revalidar a aba consome a pendência de atualização; diffFetchedAt
  // dispara a limpeza sempre que um novo diff chega.
  useEffect(() => {
    if (wsId && diffFetchedAt) clearPending(wsId, "files");
  }, [wsId, diffFetchedAt, clearPending]);

  const statusByPath = useMemo(() => {
    const byPath = new Map<string, string>();
    for (const f of diffSummary?.files ?? [])
      byPath.set(norm(f.path), f.status);
    return byPath;
  }, [diffSummary]);

  useWorkbenchSWR({
    key: `files-diff-badges:${wsId}`,
    hasCache: diffSummary !== null,
    isStale: () => Date.now() - diffFetchedAt > WORKBENCH_STALE_MS,
    revalidate: async () => {
      if (!wsId) return;
      const data = await fetchDiffSummary(wsId);
      if (data) setGitSummary(wsId, data);
    },
    skip: !wsId,
  });

  // Estado de criação inline
  const [creating, setCreating] = useState<CreatingState | null>(null);

  // ── Confirmação de delete (C.12: Radix Dialog, não window.confirm) ───────
  const [deleteConfirm, setDeleteConfirm] = useState<{
    path: string;
    name: string;
    permanent: boolean;
  } | null>(null);

  // ── Edição inline do viewer ──────────────────────────────────────────────
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  const handleStartEdit = useCallback(() => {
    setEditContent(openContent?.content ?? "");
    setIsEditing(true);
  }, [openContent]);

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setEditContent("");
  }, []);

  async function handleSaveEdit(force = false) {
    if (!wsId || !openPath) return;
    setSaving(true);
    const sha = force ? null : (openContent?.sha256 ?? null);
    const result = await apiUpdateFile(wsId, openPath, editContent, sha);
    setSaving(false);
    if (result.ok) {
      setIsEditing(false);
      const refreshed = await fetchFile(wsId, openPath);
      if (refreshed) setFileContent(wsId, openPath, refreshed);
      useToastStore.getState().success(m.workbench_files_save());
    } else if (result.conflict) {
      useToastStore.getState().error(m.workbench_files_conflict_title(), {
        action: {
          label: m.workbench_files_force_save(),
          onClick: () => void handleSaveEdit(true),
        },
      });
    } else {
      useToastStore.getState().error(m.workbench_files_save_error());
    }
  }

  // Reset estado de edição ao trocar de arquivo — comparação durante o
  // render (não num effect), como recomendado em
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevOpenPathForEdit, setPrevOpenPathForEdit] = useState(openPath);
  if (openPath !== prevOpenPathForEdit) {
    setPrevOpenPathForEdit(openPath);
    setIsEditing(false);
    setEditContent("");
  }

  // ── Busca em conteúdo (A.5) ─────────────────────────────────────────────
  const [searchMode, setSearchMode] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [replaceQuery, setReplaceQuery] = useState("");
  const [replacing, setReplacing] = useState(false);
  // Linha destacada no viewer quando resultado de busca é aberto.
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  // Scroll automático para a linha destacada quando o arquivo abre.
  useEffect(() => {
    if (!openPath || highlightLine === null) return;
    highlightRef.current?.scrollIntoView({ block: "center" });
  }, [openPath, highlightLine]);

  // Busca com debounce de 350ms — dispara quando query muda.
  useEffect(() => {
    if (!searchMode || !wsId) return;
    if (searchQuery.trim().length < 2) {
      // Sem termo suficiente pra buscar no backend — limpa o resultado
      // anterior em vez de disparar rede.
      // oxlint-disable-next-line react/set-state-in-effect
      setSearchResults(null);
      return;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      const res = await apiFsSearch(wsId, searchQuery.trim());
      setSearching(false);
      setSearchResults(res);
    }, 350);
    return () => clearTimeout(timer);
  }, [searchQuery, searchMode, wsId]);

  // Agrupa hits por arquivo para exibição.
  const searchGrouped = useMemo(() => {
    if (!searchResults) return [];
    const map = new Map<string, SearchHit[]>();
    for (const hit of searchResults.hits) {
      const list = map.get(hit.path) ?? [];
      list.push(hit);
      map.set(hit.path, list);
    }
    return Array.from(map.entries());
  }, [searchResults]);

  const handleReplaceAll = useCallback(async () => {
    if (!wsId || !searchQuery.trim() || !replaceQuery || !searchResults) return;
    setReplacing(true);
    try {
      const filePaths = Array.from(
        new Set(searchResults.hits.map((h) => h.path)),
      );
      const fileDatas = await Promise.all(
        filePaths.map((p) => fetchFile(wsId, p)),
      );
      await Promise.all(
        filePaths.map(async (filePath, i) => {
          const fileData = fileDatas[i];
          if (!fileData || fileData.content === undefined) return;
          const updated = fileData.content.replaceAll(
            searchQuery.trim(),
            replaceQuery,
          );
          if (updated === fileData.content) return;
          await apiUpdateFile(wsId, filePath, updated, fileData.sha256 ?? null);
          setFileContent(wsId, filePath, {
            ...fileData,
            content: updated,
            sha256: null,
          });
        }),
      );
      const res = await apiFsSearch(wsId, searchQuery.trim());
      setSearchResults(res);
    } finally {
      setReplacing(false);
    }
  }, [wsId, searchQuery, replaceQuery, searchResults, setFileContent]);

  // ── Histórico de arquivo (A.6) ───────────────────────────────────────────
  const [historyMode, setHistoryMode] = useState(false);
  const [historyPath, setHistoryPath] = useState<string | null>(null);
  const [historyEntries, setHistoryEntries] = useState<FileLogEntry[] | null>(
    null,
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historicSha, setHistoricSha] = useState<string | null>(null);
  const [historicContent, setHistoricContent] =
    useState<ShowFileAtRevResponse | null>(null);
  const [historicLoading, setHistoricLoading] = useState(false);

  // Fecha o histórico quando o arquivo aberto muda (navegação na árvore) —
  // comparação durante o render (não num effect), como recomendado em
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevOpenPathForHistory, setPrevOpenPathForHistory] =
    useState(openPath);
  if (openPath !== prevOpenPathForHistory) {
    setPrevOpenPathForHistory(openPath);
    if (historyMode && openPath !== historyPath) {
      setHistoryMode(false);
      setHistoryEntries(null);
      setHistoricSha(null);
      setHistoricContent(null);
    }
  }

  const handleOpenFile = useCallback(
    (path: string) => {
      if (!wsId) return;
      setOpenFile(wsId, path);
    },
    [wsId, setOpenFile],
  );

  const handleCloseViewer = useCallback(() => {
    if (!wsId) return;
    setOpenFile(wsId, null);
  }, [wsId, setOpenFile]);

  // Refresh: invalida cache e força re-fetch
  const handleRefresh = useCallback(() => {
    if (!wsId) return;
    invalidateFiles(wsId);
  }, [wsId, invalidateFiles]);

  // Criar arquivo ou pasta
  const handleRequestCreate = useCallback(
    (type: "file" | "dir", parentDir: string) => {
      setCreating({ type, parentDir });
    },
    [],
  );

  const handleInlineCreate = useCallback(
    async (name: string) => {
      if (!wsId || !creating) return;
      setCreating(null);
      const relPath = creating.parentDir
        ? `${creating.parentDir}/${name}`
        : name;
      const ok = await apiFsCreate(wsId, creating.type, relPath);
      if (ok) invalidateFiles(wsId);
    },
    [wsId, creating, invalidateFiles],
  );

  const handleCancelCreate = useCallback(() => {
    setCreating(null);
  }, []);

  // Abre arquivo a partir de um resultado de busca, destacando a linha.
  const handleOpenHit = useCallback(
    (path: string, lineNumber: number) => {
      setHighlightLine(lineNumber);
      handleOpenFile(path);
    },
    [handleOpenFile],
  );

  // Abre o painel de histórico para o arquivo atualmente aberto.
  const handleOpenHistory = useCallback(async () => {
    if (!wsId || !openPath) return;
    setHistoryMode(true);
    setHistoryPath(openPath);
    setHistoryEntries(null);
    setHistoricSha(null);
    setHistoricContent(null);
    setHistoryLoading(true);
    const res = await apiFsGitLogFile(wsId, openPath);
    setHistoryLoading(false);
    setHistoryEntries(res?.entries ?? []);
  }, [wsId, openPath]);

  // Seleciona um commit do histórico e carrega o conteúdo do arquivo naquele ponto.
  const handleSelectHistoricSha = useCallback(
    async (sha: string) => {
      if (!wsId || !historyPath) return;
      setHistoricSha(sha);
      setHistoricLoading(true);
      const res = await apiFsGitShow(wsId, sha, historyPath);
      setHistoricLoading(false);
      setHistoricContent(res);
    },
    [wsId, historyPath],
  );

  // Deletar arquivo/pasta com confirmação via ConfirmDialog (Radix — C.12).
  const handleDelete = useCallback(
    (path: string, name: string, permanent = false) => {
      if (!wsId) return;
      setDeleteConfirm({ path, name, permanent });
    },
    [wsId],
  );

  const handleDeleteConfirmed = useCallback(async () => {
    if (!wsId || !deleteConfirm) return;
    const { path, permanent } = deleteConfirm;
    setDeleteConfirm(null);
    const ok = await apiFsDelete(wsId, path, permanent);
    if (ok) {
      invalidateFiles(wsId);
      if (openPath === path) setOpenFile(wsId, null);
    }
  }, [wsId, deleteConfirm, openPath, invalidateFiles, setOpenFile]);

  // Mover por drag-and-drop: soltar um arquivo/pasta arrastado sobre outra
  // pasta (ou a raiz). `targetDir === ""` é a raiz do workspace. No-op
  // silencioso quando o alvo já é o diretório atual do item (mesmo lugar) —
  // evita um toast de erro "já existe" pelo caso mais comum de drop.
  const handleMoveInto = useCallback(
    async (sourcePath: string, targetDir: string) => {
      if (!wsId) return;
      const basename = sourcePath.includes("/")
        ? sourcePath.split("/").pop()!
        : sourcePath;
      const sourceParent = sourcePath.includes("/")
        ? sourcePath.split("/").slice(0, -1).join("/")
        : "";
      if (sourceParent === targetDir) return;
      // Nunca move uma pasta pra dentro de si mesma ou de um descendente.
      if (targetDir === sourcePath || targetDir.startsWith(`${sourcePath}/`)) {
        return;
      }

      const toPath = targetDir ? `${targetDir}/${basename}` : basename;
      const result = await apiFsMove(wsId, sourcePath, toPath);
      if (!result.ok) {
        const msg =
          result.message === "Já existe um arquivo ou pasta com esse nome."
            ? m.workbench_files_rename_exists()
            : m.workbench_files_rename_error();
        useToastStore.getState().error(msg);
        return;
      }
      invalidateFiles(wsId);
      if (openPath === sourcePath) setOpenFile(wsId, toPath);
    },
    [wsId, invalidateFiles, openPath, setOpenFile],
  );

  // ── Gerenciador de .gitignore (A.10) ─────────────────────────────────────
  const [gitignoreOpen, setGitignoreOpen] = useState(false);
  const [gitignoreDraft, setGitignoreDraft] = useState<string>("");
  const [gitignoreSaving, setGitignoreSaving] = useState(false);
  const [gitignorePreview, setGitignorePreview] = useState<string[]>([]);
  const [previewPattern, setPreviewPattern] = useState("");

  const handleOpenGitignore = useCallback(async () => {
    if (!wsId) return;
    setGitignoreOpen(true);
    const res = await fetch(
      `/workspaces/${encodeURIComponent(wsId)}/fs/gitignore`,
    );
    if (res.ok) {
      const data = await res.json();
      setGitignoreDraft((data.content as string) ?? "");
    }
  }, [wsId]);

  const handleSaveGitignore = useCallback(async () => {
    if (!wsId) return;
    setGitignoreSaving(true);
    const lines = gitignoreDraft.split("\n");
    await fetch(`/workspaces/${encodeURIComponent(wsId)}/fs/gitignore`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines }),
    });
    setGitignoreSaving(false);
    setGitignoreOpen(false);
  }, [wsId, gitignoreDraft]);

  useEffect(() => {
    if (!previewPattern.trim() || !wsId || !gitignoreOpen) {
      // Sem padrão pra prever no backend — limpa o resultado anterior em
      // vez de disparar rede.
      // oxlint-disable-next-line react/set-state-in-effect
      setGitignorePreview([]);
      return;
    }
    const timer = setTimeout(async () => {
      const qs = new URLSearchParams({ pattern: previewPattern.trim() });
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/fs/gitignore-preview?${qs}`,
      );
      if (res.ok) {
        const data = await res.json();
        setGitignorePreview((data.matched as string[]) ?? []);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [previewPattern, wsId, gitignoreOpen]);

  // Ctrl+N → novo arquivo na raiz; Ctrl+Shift+N → nova pasta na raiz
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey) return;
      if (e.key === "N" || e.key === "n") {
        if (e.shiftKey) {
          e.preventDefault();
          handleRequestCreate("dir", "");
        } else {
          e.preventDefault();
          handleRequestCreate("file", "");
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleRequestCreate]);

  // SWR para o conteúdo do arquivo aberto
  useWorkbenchSWR({
    key: `file:${wsId}:${openPath ?? ""}`,
    hasCache: openContent !== undefined,
    isStale: false,
    revalidate: async () => {
      if (!wsId || !openPath) return;
      const data = await fetchFile(wsId, openPath);
      if (data) setFileContent(wsId, openPath, data);
    },
    skip: !openPath || !wsId,
  });

  if (!workspace) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center">
        <FolderOpen className="w-8 h-8 text-muted-foreground/50" />
        <p className="text-xs text-muted-foreground">
          {m.workbench_files_no_workspace()}
        </p>
        <WorkspaceSelector />
      </div>
    );
  }

  const showViewer = openPath !== null;
  const loadingFile = showViewer && openContent === undefined;

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar VS Code-like */}
      <div className="flex items-center gap-0.5 px-2 py-1 border-b border-border/60">
        <span className="text-[10px] font-medium text-muted-foreground truncate flex-1 select-none">
          {workspace.name}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => handleRequestCreate("file", "")}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              aria-label={m.tooltip_files_new_file()}
              data-testid="files-new-file-btn"
            >
              <FilePlus className="w-3.5 h-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {m.tooltip_files_new_file()}
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => handleRequestCreate("dir", "")}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              aria-label={m.tooltip_files_new_folder()}
            >
              <FolderPlus className="w-3.5 h-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {m.tooltip_files_new_folder()}
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={handleRefresh}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              aria-label={m.tooltip_files_refresh()}
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {m.tooltip_files_refresh()}
          </TooltipContent>
        </Tooltip>
        {/* .gitignore manager (A.10) */}
        {workspace.is_git_repo && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={handleOpenGitignore}
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                aria-label={m.tooltip_files_gitignore()}
              >
                <Filter className="w-3.5 h-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {m.tooltip_files_gitignore()}
            </TooltipContent>
          </Tooltip>
        )}
        {/* Toggle de busca em conteúdo (A.5) */}
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => {
                if (searchMode) {
                  setSearchMode(false);
                  setSearchQuery("");
                  setSearchResults(null);
                  setHighlightLine(null);
                } else {
                  setSearchMode(true);
                }
              }}
              className={`p-1 rounded transition-colors ${
                searchMode
                  ? "bg-primary/20 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
              }`}
              aria-label={m.tooltip_files_search()}
              aria-pressed={searchMode}
            >
              <Search className="w-3.5 h-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {m.tooltip_files_search()}
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Filtro de nomes ou busca em conteúdo */}
      {searchMode ? (
        <div className="px-2 py-1.5 border-b border-border/60 space-y-1">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
            <Input
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              type="search"
              autoComplete="off"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={m.workbench_files_search_placeholder()}
              className="h-7 text-xs pl-7 pr-6"
            />
            {searchQuery && (
              <button
                onClick={() => {
                  setSearchQuery("");
                  setSearchResults(null);
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                title={m.workbench_files_cancel()}
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
          <div className="flex gap-1">
            <div className="relative flex-1">
              <Pencil className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
              <Input
                type="text"
                autoComplete="off"
                value={replaceQuery}
                onChange={(e) => setReplaceQuery(e.target.value)}
                placeholder={m.workbench_files_replace_placeholder()}
                className="h-7 text-xs pl-7"
              />
            </div>
            <button
              onClick={() => void handleReplaceAll()}
              disabled={
                replacing ||
                !searchQuery.trim() ||
                !replaceQuery ||
                !searchResults?.hits.length
              }
              className="shrink-0 px-2 h-7 rounded text-xs bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title={m.workbench_files_replace_all()}
            >
              {replacing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                m.workbench_files_replace_all()
              )}
            </button>
          </div>
          {searching && (
            <div className="flex justify-center pt-1">
              <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
            </div>
          )}
        </div>
      ) : (
        <div className="px-2 py-1.5 border-b border-border/60">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
            <Input
              value={filter}
              onChange={(e) => setFilesFilter(wsId, e.target.value)}
              placeholder={m.workbench_files_filter()}
              autoComplete="off"
              className="h-7 text-xs pl-7"
            />
          </div>
        </div>
      )}

      {/* Árvore (topo) + viewer (base) num split vertical arrastável */}
      <VerticalSplit
        className="flex-1"
        showBottom={showViewer}
        bottomSize={viewerHeight}
        onResize={setViewerHeight}
        top={
          <div
            className="h-full overflow-y-auto py-1"
            role="tree"
            aria-label={m.workbench_files_tree_label()}
            aria-busy={showSkeleton}
          >
            {historyMode ? (
              <>
                {/* Cabeçalho do painel de histórico */}
                <div className="flex items-center justify-between px-2 py-1 mb-1 border-b border-border/40">
                  <span className="text-[10px] font-medium text-muted-foreground truncate flex-1">
                    {m.workbench_files_history()}:{" "}
                    {historyPath?.split("/").pop()}
                  </span>
                  <button
                    onClick={() => {
                      setHistoryMode(false);
                      setHistoryEntries(null);
                      setHistoricSha(null);
                      setHistoricContent(null);
                    }}
                    className="p-0.5 text-muted-foreground hover:text-foreground"
                    title={m.workbench_close()}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
                <FileHistoryPanel
                  entries={historyEntries}
                  loading={historyLoading}
                  selectedSha={historicSha}
                  onSelectSha={handleSelectHistoricSha}
                />
              </>
            ) : searchMode ? (
              <div className="px-1">
                {searchResults !== null && searchResults.hits.length === 0 && (
                  <p className="text-[10px] text-muted-foreground text-center py-4">
                    {m.workbench_files_search_no_results()}
                  </p>
                )}
                {searchGrouped.map(([filePath, hits]) => (
                  <SearchResultGroup
                    key={filePath}
                    filePath={filePath}
                    hits={hits}
                    onOpenHit={handleOpenHit}
                  />
                ))}
                {searchResults?.truncated && (
                  <p className="text-[10px] text-muted-foreground px-2 py-1">
                    {m.workbench_files_search_truncated()}
                  </p>
                )}
              </div>
            ) : (
              <>
                <PinnedSection
                  threadId={threadId}
                  workspaceId={wsId}
                  onAddToContext={onAddToContext}
                />

                {/* O input de criação na raiz é renderizado pelo DirNode raiz
                (path="", sempre expandido). Renderizar um segundo input aqui
                fazia os dois montarem com autoFocus: o segundo roubava o foco,
                o onBlur do primeiro disparava onCancel e ambos sumiam — era o
                bug dos botões "novo arquivo"/"nova pasta" não funcionarem. */}

                <DirNode
                  threadId={threadId}
                  workspaceId={wsId}
                  path=""
                  name={workspace.name}
                  depth={0}
                  filter={filter}
                  statusByPath={statusByPath}
                  onOpenFile={handleOpenFile}
                  onAddToContext={onAddToContext}
                  onDelete={handleDelete}
                  creating={creating}
                  onInlineCreate={handleInlineCreate}
                  onCancelCreate={handleCancelCreate}
                  onRequestCreate={handleRequestCreate}
                  onMoveInto={handleMoveInto}
                />
              </>
            )}
          </div>
        }
        bottom={
          <div className="border-t border-border/60 h-full flex flex-col">
            <div className="flex items-center justify-between px-2 py-1 bg-muted/30 text-xs">
              <span className="truncate font-mono text-muted-foreground">
                {openPath ?? "…"}
              </span>
              <div className="flex items-center gap-1 shrink-0">
                {onAddToContext && openPath && (
                  <button
                    onClick={() => onAddToContext(openPath)}
                    className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                    title={m.workbench_files_add_context()}
                  >
                    <AtSign className="w-3 h-3" />
                  </button>
                )}
                {/* Botão de histórico — só para workspaces git (A.6) */}
                {workspace.is_git_repo && openPath && (
                  <button
                    onClick={handleOpenHistory}
                    className={`p-0.5 rounded transition-colors ${
                      historyMode
                        ? "text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                    title={m.workbench_files_history()}
                    aria-pressed={historyMode}
                  >
                    <History className="w-3 h-3" />
                  </button>
                )}
                {openPath &&
                  openContent?.kind !== "binary" &&
                  !openContent?.truncated && (
                    <button
                      onClick={isEditing ? handleCancelEdit : handleStartEdit}
                      className={`p-0.5 rounded transition-colors ${isEditing ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
                      title={
                        isEditing
                          ? m.workbench_files_cancel()
                          : m.workbench_files_edit()
                      }
                      aria-pressed={isEditing}
                      data-editing={isEditing ? "true" : "false"}
                      data-testid="files-edit-toggle-btn"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                  )}
                {uiMode === "assistant" && openPath && (
                  <button
                    onClick={() => openWindow(wsId, openPath)}
                    className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                    title={m.window_open_as_window()}
                    aria-label={m.window_open_as_window()}
                  >
                    <AppWindow className="w-3 h-3" />
                  </button>
                )}
                <button
                  onClick={handleCloseViewer}
                  className="text-muted-foreground hover:text-foreground px-1"
                  title={m.workbench_close()}
                >
                  ×
                </button>
              </div>
            </div>
            {/* Banner de revisão histórica (A.6) */}
            {historicSha && (
              <div className="flex items-center gap-2 px-2 py-1 bg-amber-500/10 border-b border-amber-500/20 text-[10px]">
                {historicLoading ? (
                  <Loader2 className="w-3 h-3 animate-spin text-muted-foreground shrink-0" />
                ) : (
                  <History className="w-3 h-3 text-amber-500 shrink-0" />
                )}
                <span className="text-muted-foreground truncate flex-1">
                  {m.workbench_files_history_viewing_at()}{" "}
                  <span className="font-mono text-amber-500">
                    {historicSha.slice(0, 7)}
                  </span>
                </span>
                <button
                  onClick={() => {
                    setHistoricSha(null);
                    setHistoricContent(null);
                  }}
                  className="text-muted-foreground hover:text-foreground shrink-0"
                >
                  {m.workbench_files_history_back()}
                </button>
              </div>
            )}
            <div className="flex-1 overflow-auto p-2">
              {!historicSha && openPath && getMediaKind(openPath) ? (
                <MediaView
                  kind={getMediaKind(openPath)!}
                  workspaceId={wsId}
                  path={openPath}
                />
              ) : historicSha && historicLoading ? (
                <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              ) : historicSha && historicContent ? (
                historicContent.binary ? (
                  <p className="text-xs text-muted-foreground">
                    {m.workbench_files_binary({ size: 0 })}
                  </p>
                ) : (
                  <>
                    <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                      {historicContent.content ?? ""}
                    </pre>
                    {historicContent.truncated && (
                      <p className="text-[10px] text-muted-foreground mt-2">
                        {m.workbench_files_read_only_truncated()}
                      </p>
                    )}
                  </>
                )
              ) : loadingFile ? (
                <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              ) : openContent?.kind === "binary" ? (
                <p className="text-xs text-muted-foreground">
                  {m.workbench_files_binary({ size: openContent.size })}
                </p>
              ) : openPath?.toLowerCase().match(/\.(md|markdown)$/) &&
                openContent?.content &&
                highlightLine === null ? (
                <MarkdownView content={openContent.content} />
              ) : highlightLine !== null ? (
                // Renderização linha-a-linha com destaque para busca em conteúdo
                <div className="text-xs font-mono leading-relaxed">
                  {(openContent?.content ?? "").split("\n").map((line, i) => {
                    const lineNum = i + 1;
                    return (
                      <div
                        key={i}
                        ref={
                          lineNum === highlightLine ? highlightRef : undefined
                        }
                        className={
                          lineNum === highlightLine
                            ? "bg-yellow-500/20 rounded"
                            : undefined
                        }
                      >
                        <span className="select-none text-muted-foreground inline-block w-8 text-right mr-3 text-[10px]">
                          {lineNum}
                        </span>
                        <span className="whitespace-pre-wrap break-all">
                          {line}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ) : isEditing ? (
                <div className="flex flex-col gap-1 h-full">
                  <textarea
                    className="flex-1 w-full font-mono text-xs bg-muted/20 border border-border/60 rounded p-2 resize-none outline-none focus:border-primary min-h-[12rem]"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    spellCheck={false}
                    data-testid="inline-editor-textarea"
                  />
                  <div className="flex gap-2 justify-end shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleCancelEdit}
                      className="h-6 text-xs"
                    >
                      {m.workbench_files_cancel()}
                    </Button>
                    <Button
                      size="sm"
                      disabled={saving}
                      onClick={() => void handleSaveEdit()}
                      className="h-6 text-xs"
                      data-testid="files-save-btn"
                    >
                      {saving ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        m.workbench_files_save()
                      )}
                    </Button>
                  </div>
                </div>
              ) : (
                <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                  {openContent?.content ?? ""}
                </pre>
              )}
              {openContent?.truncated && (
                <p className="text-[10px] text-muted-foreground mt-2">
                  {m.workbench_files_read_only_truncated()}
                </p>
              )}
            </div>
          </div>
        }
      />

      {/* Dialog .gitignore (A.10) */}
      <Dialog
        open={gitignoreOpen}
        onOpenChange={(o) => {
          if (!o) setGitignoreOpen(false);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{m.workbench_files_gitignore_title()}</DialogTitle>
            <DialogDescription>
              {m.workbench_files_gitignore_desc()}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <textarea
              className="font-mono text-xs bg-muted/30 border border-border/60 rounded p-2 h-48 resize-none outline-none focus:border-primary"
              value={gitignoreDraft}
              onChange={(e) => setGitignoreDraft(e.target.value)}
              spellCheck={false}
            />
            <div className="flex gap-2 items-center">
              <input
                className="flex-1 text-xs bg-background border border-border/60 rounded px-2 py-1 outline-none focus:border-primary"
                placeholder={m.workbench_files_gitignore_preview_placeholder()}
                value={previewPattern}
                onChange={(e) => setPreviewPattern(e.target.value)}
              />
            </div>
            {gitignorePreview.length > 0 && (
              <div className="border border-border/40 rounded p-2 max-h-28 overflow-y-auto">
                <p className="text-[10px] text-muted-foreground mb-1">
                  {m.workbench_files_gitignore_preview_matches({
                    n: gitignorePreview.length,
                  })}
                </p>
                {gitignorePreview.map((f) => (
                  <p
                    key={f}
                    className="text-[10px] font-mono text-foreground/80"
                  >
                    {f}
                  </p>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setGitignoreOpen(false)}
            >
              {m.workbench_files_cancel()}
            </Button>
            <Button
              size="sm"
              disabled={gitignoreSaving}
              onClick={handleSaveGitignore}
            >
              {gitignoreSaving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                m.workbench_files_save()
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Confirmação de delete (Radix Dialog — acessível) */}
      <ConfirmDialog
        open={deleteConfirm !== null}
        title={
          deleteConfirm?.permanent
            ? `Deletar permanentemente "${deleteConfirm.name}"?`
            : `Mover "${deleteConfirm?.name}" para a Lixeira?`
        }
        description={
          deleteConfirm?.permanent
            ? "Esta ação não pode ser desfeita."
            : undefined
        }
        confirmLabel={deleteConfirm?.permanent ? "Deletar" : "Mover"}
        variant={deleteConfirm?.permanent ? "destructive" : "default"}
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setDeleteConfirm(null)}
      />
    </div>
  );
}

"use client";

/**
 * WorkspaceTrustDialog
 *
 * Fluxo de "trust folder": directory browser para escolher uma pasta,
 * confirmação explícita de confiança (explica os guard rails de escopo) e
 * checkbox para inicializar git se a pasta ainda não for um repositório.
 *
 * Ao confiar, registra a pasta como workspace ativo via store.create().
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  Cloud,
  CornerDownLeft,
  Database,
  Folder,
  FolderPlus,
  GitBranch,
  HardDrive,
  Loader2,
  Monitor,
  RefreshCw,
  Server,
  Upload,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DRIVES_PSEUDO_PATH,
  useWorkspacesStore,
  type BrowseResult,
  type CodespaceSummary,
} from "@/lib/stores/workspaces-store";
import { useNetworkStatus } from "@/lib/hooks/use-network-status";
import { useIngestPreview } from "@/lib/hooks/use-ingest-preview";
import { useRagJobsStore } from "@/lib/stores/rag-jobs-store";
import {
  RagSettingsButton,
  RagSettingsSlidePanel,
  useRagSettings,
} from "@/components/workbench/rag-settings-panel";
import { m } from "@/lib/paraglide/messages";

type Tab = "local" | "ssh" | "codespace";

interface WorkspaceTrustDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `trust`: adiciona pasta como workspace confiável (default).
   *  `ingest`: usa o directory browser apenas para escolher a pasta e
   *  encaminha o caminho para o chat indexar via tool ingest_docs. */
  mode?: "trust" | "ingest";
  /** Callback opcional disparado no confirmar — recebe o path absoluto. */
  onConfirmPath?: (path: string) => void;
  /** Pré-navega para esse caminho ao abrir. */
  initialPath?: string;
}

export function WorkspaceTrustDialog({
  open,
  onOpenChange,
  mode = "trust",
  onConfirmPath,
  initialPath,
}: WorkspaceTrustDialogProps) {
  // Todo o fluxo (browse, trust, SSH, codespaces) depende do backend.
  const { offline } = useNetworkStatus();
  const create = useWorkspacesStore((s) => s.create);
  const getActive = useWorkspacesStore((s) => s.getActive);
  const activeWorkspaceId = useWorkspacesStore((s) => s.active_id);
  const ragStart = useRagJobsStore((s) => s.start);
  const createRemote = useWorkspacesStore((s) => s.createRemote);
  const listSshKeys = useWorkspacesStore((s) => s.listSshKeys);
  const uploadSshKey = useWorkspacesStore((s) => s.uploadSshKey);
  const testSsh = useWorkspacesStore((s) => s.testSsh);
  const listCodespaces = useWorkspacesStore((s) => s.listCodespaces);

  const [tab, setTab] = useState<Tab>("local");
  const [listing, setListing] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [gitInit, setGitInit] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [pathInput, setPathInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Criar nova pasta: input inline aberto sob demanda, dentro do diretório
  // atualmente listado.
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [folderSubmitting, setFolderSubmitting] = useState(false);

  // Ingest (mode="ingest"): filtros de extensão por inclusão/exclusão
  // (separados por vírgula — ex. "xml, tscn"). includeExts restringe os
  // formatos indexados; excludeExts remove formatos específicos.
  const [includeExts, setIncludeExts] = useState("");
  const [excludeExts, setExcludeExts] = useState("");
  const [bucketName, setBucketName] = useState("");
  const [ingestJobId, setIngestJobId] = useState<string | null>(null);
  const ragSettings = useRagSettings();
  const ingestJob = useRagJobsStore((s) =>
    ingestJobId ? s.jobs[ingestJobId] : null,
  );
  // Espelha o mesmo atalho de tipo hardcoded "all" que handleConfirm já
  // envia ao ragStart real — o preview mostra exatamente o que a indexação
  // de fato indexaria.
  const ingestPreview = useIngestPreview(
    mode === "ingest" ? activeWorkspaceId : null,
    mode === "ingest" && tab === "local" ? (listing?.path ?? "") : "",
    "all",
    includeExts,
    excludeExts,
  );

  // SSH state
  const [sshHost, setSshHost] = useState("");
  const [sshPath, setSshPath] = useState("");
  const [sshKeys, setSshKeys] = useState<string[]>([]);
  const [sshKeyId, setSshKeyId] = useState<string>("");
  const [sshTesting, setSshTesting] = useState(false);
  const [sshTestResult, setSshTestResult] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  // Codespace state
  const [codespaces, setCodespaces] = useState<CodespaceSummary[]>([]);
  const [codespaceLoading, setCodespaceLoading] = useState(false);
  const [codespaceAvailable, setCodespaceAvailable] = useState(true);
  const [codespaceMessage, setCodespaceMessage] = useState("");
  // Evita resetar o input enquanto o usuário digita um path novo —
  // só sincroniza quando a navegação (clique/Enter) muda o listing.path.
  const lastLoadedPathRef = useRef<string | null>(null);
  const requestEpochRef = useRef(0);
  const folderSubmissionRef = useRef(0);

  /** Fetch direto: precisamos distinguir 403 (fora de safe-root) de
   *  outros erros para mostrar mensagem inline. O `browse` do store
   *  achata erros em `null` e perde essa informação. */
  const load = useCallback(async (path?: string) => {
    const epoch = ++requestEpochRef.current;
    // A navegação invalida a criação associada ao diretório anterior. O
    // request antigo pode terminar depois, mas não deve manter a UI travada
    // nem liberar o estado de uma criação iniciada no novo diretório.
    folderSubmissionRef.current += 1;
    setFolderSubmitting(false);
    const requestedPath = path ?? lastLoadedPathRef.current;
    // Keep the path that is currently being requested available for retry,
    // including when this request fails before a listing is returned.
    lastLoadedPathRef.current = requestedPath ?? null;
    setLoading(true);
    setError(null);
    setListing(null);
    // Navegar pra outro diretório fecha um formulário de "nova pasta"
    // pendente — ele se referia ao diretório anterior.
    setCreatingFolder(false);
    setNewFolderName("");
    try {
      const q = requestedPath
        ? `?path=${encodeURIComponent(requestedPath)}`
        : "";
      const res = await fetch(`/workspaces/browse${q}`, {
        credentials: "include",
      });
      if (res.status === 403) {
        const data = await res.json().catch(() => ({}));
        if (epoch !== requestEpochRef.current) return;
        setError(data.detail ?? "Caminho fora das pastas seguras.");
        return;
      }
      if (!res.ok) {
        if (epoch !== requestEpochRef.current) return;
        setError(`Erro ao listar (${res.status}).`);
        return;
      }
      // Se o proxy retornou index.html (backend offline), o content-type é
      // text/html e o JSON.parse falharia — detectar antes de tentar.
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) {
        if (epoch !== requestEpochRef.current) return;
        setError(
          "Servidor indisponível. Inicie o backend e reabra este diálogo.",
        );
        return;
      }
      const data = (await res.json()) as BrowseResult;
      if (epoch !== requestEpochRef.current) return;
      setListing(data);
      lastLoadedPathRef.current = data.path;
      setPathInput(data.path);
    } catch (e) {
      if (epoch !== requestEpochRef.current) return;
      setError(e instanceof Error ? e.message : "Falha de rede.");
    } finally {
      if (epoch === requestEpochRef.current) setLoading(false);
    }
  }, []);

  // Reseta o formulário sempre que o diálogo reabre — comparação durante o
  // render (não num effect), como recomendado em
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setListing(null);
      setGitInit(true);
      setError(null);
      setIngestJobId(null);
      setIncludeExts("");
      setExcludeExts("");
      setPathInput(initialPath ?? "");
      setTab("local");
      setSshHost("");
      setSshPath("");
      setSshKeyId("");
      setSshTestResult(null);
      setCodespaces([]);
    }
  }

  useEffect(() => {
    if (open) {
      lastLoadedPathRef.current = null;
      // Busca a listagem do path (rede) ao abrir o diálogo.
      // oxlint-disable-next-line react/set-state-in-effect
      void load(initialPath || undefined);
    }
  }, [open, load, initialPath]);

  // Carrega SSH keys e codespaces ao trocar de tab.
  useEffect(() => {
    if (!open) return;
    if (tab === "ssh") {
      void (async () => {
        const keys = await listSshKeys();
        setSshKeys(keys);
      })();
    } else if (tab === "codespace") {
      // Kicka a busca de codespaces (rede); loading precisa ligar antes do await.
      // oxlint-disable-next-line react/set-state-in-effect
      setCodespaceLoading(true);
      void (async () => {
        const data = await listCodespaces();
        setCodespaces(data.codespaces);
        setCodespaceAvailable(data.available);
        setCodespaceMessage(data.message);
        setCodespaceLoading(false);
      })();
    }
  }, [open, tab, listSshKeys, listCodespaces]);

  const handleGo = () => {
    const target = pathInput.trim();
    if (!target || target === lastLoadedPathRef.current) return;
    void load(target);
  };

  /** Recarrega o diretório atual — `load` não tem o guard de "path
   * inalterado" de `handleGo`, então chamar direto já força o refetch. */
  const handleReload = () => {
    const path = listing?.path ?? lastLoadedPathRef.current ?? pathInput.trim();
    if (path) void load(path);
  };

  const handleCreateFolder = async () => {
    const name = newFolderName.trim();
    if (!listing || !name) return;
    const submissionId = ++folderSubmissionRef.current;
    setFolderSubmitting(true);
    const navigationEpoch = requestEpochRef.current;
    setError(null);
    try {
      const res = await fetch("/workspaces/browse/mkdir", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: listing.path, name }),
      });
      if (res.status === 400) {
        if (navigationEpoch !== requestEpochRef.current) return;
        setError(m.workspace_new_folder_error_invalid_name());
        return;
      }
      if (res.status === 409) {
        if (navigationEpoch !== requestEpochRef.current) return;
        setError(m.workspace_new_folder_error_conflict());
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (navigationEpoch !== requestEpochRef.current) return;
        setError(data.detail ?? `Erro ao criar pasta (${res.status}).`);
        return;
      }
      const data = (await res.json()) as BrowseResult;
      if (navigationEpoch !== requestEpochRef.current) return;
      const createdPath =
        data.created_path ??
        data.entries.find((entry) => entry.is_dir && entry.name === name)?.path;
      if (createdPath) {
        setCreatingFolder(false);
        setNewFolderName("");
        await load(createdPath);
        return;
      }

      // Compatibilidade com servidores antigos que ainda não retornam
      // created_path nem a entrada criada na resposta.
      if (navigationEpoch !== requestEpochRef.current) return;
      setListing(data);
      lastLoadedPathRef.current = data.path;
      setPathInput(data.path);
      setNewFolderName("");
      setCreatingFolder(false);
    } catch (e) {
      if (navigationEpoch !== requestEpochRef.current) return;
      setError(e instanceof Error ? e.message : "Falha de rede.");
    } finally {
      if (submissionId === folderSubmissionRef.current) {
        setFolderSubmitting(false);
      }
    }
  };

  const handleUploadKey = async (file: File) => {
    const keyId = await uploadSshKey(file);
    if (keyId) {
      const keys = await listSshKeys();
      setSshKeys(keys);
      setSshKeyId(keyId);
    }
  };

  const handleTestSsh = async () => {
    if (!sshHost.trim()) return;
    setSshTesting(true);
    setSshTestResult(null);
    const result = await testSsh(sshHost.trim(), sshKeyId || null);
    setSshTesting(false);
    setSshTestResult(result);
  };

  const handleConfirmSsh = async () => {
    if (!sshHost.trim()) return;
    setSubmitting(true);
    const ws = await createRemote({
      transport: "ssh",
      remote_host: sshHost.trim(),
      remote_path: sshPath.trim() || undefined,
      ssh_key_id: sshKeyId || null,
    });
    setSubmitting(false);
    if (ws) onOpenChange(false);
  };

  const handleConfirmCodespace = async (cs: CodespaceSummary) => {
    setSubmitting(true);
    const ws = await createRemote({
      transport: "codespace",
      codespace_name: cs.name,
      name: cs.repository || cs.name,
    });
    setSubmitting(false);
    if (ws) onOpenChange(false);
  };

  const handleConfirm = async () => {
    if (!listing || folderSubmitting || loading) return;
    if (mode === "ingest") {
      const wsId = getActive()?.id;
      if (!wsId) {
        setError(m.workspace_ingest_no_workspace());
        return;
      }
      onConfirmPath?.(listing.path);
      setSubmitting(true);
      // Filtros de extensão (CSV de formatos) — substituem o atalho de tipos.
      const includeVal = includeExts.trim();
      const excludeVal = excludeExts.trim();
      const jobId = await ragStart(wsId, listing.path, "all", {
        includeExts: includeVal || undefined,
        excludeExts: excludeVal || undefined,
        bucketName: bucketName.trim() || undefined,
      });
      setSubmitting(false);
      if (jobId) setIngestJobId(jobId);
      else setError(m.workspace_ingest_failed());
      return;
    }
    setSubmitting(true);
    const result = await create(listing.path, {
      trust: true,
      git_init: gitInit,
    });
    setSubmitting(false);
    if (result.ok) onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {mode === "ingest"
              ? m.workspace_ingest_title()
              : m.workspace_trust_title()}
            {/* Configurações do RAG (reranker/embedding/tipos de arquivo)
                acessíveis direto daqui — antes só existiam na aba Memória,
                obrigando fechar o modal de indexação pra ajustar algo que
                afeta a própria indexação em andamento. Inline logo após o
                título (não empurrado pra direita) — o X de fechar do Dialog
                é `absolute top-4 right-4`, e um botão nesse mesmo canto
                quase se sobrepõe a ele. */}
            {mode === "ingest" && (
              <RagSettingsButton
                open={ragSettings.open}
                onToggle={ragSettings.toggle}
              />
            )}
          </DialogTitle>
          <DialogDescription>
            {mode === "ingest"
              ? m.workspace_ingest_desc()
              : m.workspace_trust_desc()}
          </DialogDescription>
        </DialogHeader>

        {mode === "ingest" && (
          <RagSettingsSlidePanel
            open={ragSettings.open}
            close={ragSettings.close}
            settings={ragSettings.settings}
            collections={ragSettings.collections}
            patch={ragSettings.patch}
            loadCollections={ragSettings.loadCollections}
            deleteCollection={ragSettings.deleteCollection}
          />
        )}

        {mode === "ingest" && ingestJobId ? (
          <div className="space-y-3 py-2">
            <div className="flex items-center gap-2 text-sm">
              {ingestJob?.status === "done" ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
              ) : ingestJob?.status === "no_files" ? (
                <Database className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : ingestJob?.status === "failed" ||
                ingestJob?.status === "paused" ? (
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
              ) : (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
              )}
              <span className="truncate font-mono text-xs text-muted-foreground">
                {ingestJob?.path}
              </span>
            </div>
            {/* Erro de indexação (ex.: rate limit do Cohere) — refletido no
            modal, não só na Memory tab. */}
            {(ingestJob?.status === "failed" ||
              ingestJob?.status === "paused") &&
            ingestJob?.errorReason ? (
              <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                <span className="break-words text-amber-700 dark:text-amber-400">
                  {ingestJob.errorReason}
                </span>
              </div>
            ) : (
              <>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted/60">
                  <div
                    className="h-full bg-primary transition-all duration-300"
                    style={{
                      width: `${
                        ingestJob && ingestJob.total > 0
                          ? Math.min(
                              100,
                              Math.round(
                                (ingestJob.processed / ingestJob.total) * 100,
                              ),
                            )
                          : ingestJob?.status === "done"
                            ? 100
                            : 5
                      }%`,
                    }}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  {ingestJob?.status === "no_files"
                    ? m.workspace_ingest_no_files()
                    : `${ingestJob?.processed ?? 0} / ${ingestJob?.total ?? 0} ${m.workspace_ingest_chunks()}`}
                </p>
              </>
            )}
            <DialogFooter>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                {m.workspace_ingest_minimize()}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            {/* Tabs Local / SSH / Codespace.
            mode="ingest" mantém só Local: ingest é fluxo local-only. */}
            {mode === "trust" && (
              <div className="flex gap-1 border-b border-border/60 pb-1">
                <button
                  type="button"
                  onClick={() => setTab("local")}
                  className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors ${
                    tab === "local"
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Monitor className="w-3.5 h-3.5" /> {m.workspace_tab_local()}
                </button>
                <button
                  type="button"
                  onClick={() => setTab("ssh")}
                  className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors ${
                    tab === "ssh"
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Server className="w-3.5 h-3.5" />
                  {m.workspace_transport_ssh()}
                </button>
                <button
                  type="button"
                  onClick={() => setTab("codespace")}
                  className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors ${
                    tab === "codespace"
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Cloud className="w-3.5 h-3.5" />
                  {m.workspace_transport_codespace()}
                </button>
              </div>
            )}

            {tab === "local" && (
              <>
                {mode === "ingest" && (
                  <div className="space-y-1.5">
                    {/* Incluir/excluir lado a lado — eram 2 blocos empilhados
                        (label + input cada) ocupando o dobro da altura à
                        toa, sendo campos conceitualmente paralelos. */}
                    <div className="grid grid-cols-2 gap-1.5">
                      <div className="space-y-0.5">
                        <label className="text-xs text-muted-foreground">
                          {m.workspace_ingest_include_label()}
                        </label>
                        <Input
                          className="h-8 text-xs"
                          value={includeExts}
                          onChange={(e) => setIncludeExts(e.target.value)}
                          placeholder={m.workspace_ingest_include_placeholder()}
                        />
                      </div>
                      <div className="space-y-0.5">
                        <label className="text-xs text-muted-foreground">
                          {m.workspace_ingest_exclude_label()}
                        </label>
                        <Input
                          className="h-8 text-xs"
                          value={excludeExts}
                          onChange={(e) => setExcludeExts(e.target.value)}
                          placeholder={m.workspace_ingest_exclude_placeholder()}
                        />
                      </div>
                    </div>
                    <div className="space-y-0.5">
                      <label className="text-xs text-muted-foreground">
                        {m.workspace_ingest_bucket_name_label()}
                      </label>
                      <Input
                        className="h-8 text-xs"
                        value={bucketName}
                        onChange={(e) => setBucketName(e.target.value)}
                        placeholder={m.workspace_ingest_bucket_name_placeholder()}
                      />
                    </div>
                    <div
                      className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      data-testid="workspace-ingest-preview"
                    >
                      {ingestPreview.loading ? (
                        <>
                          <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                          {m.workspace_ingest_preview_loading()}
                        </>
                      ) : ingestPreview.total === 0 ? (
                        <span>{m.workspace_ingest_preview_empty()}</span>
                      ) : (
                        <span>
                          {m.workspace_ingest_preview_count({
                            n: ingestPreview.total,
                          })}
                        </span>
                      )}
                    </div>
                    {!ingestPreview.loading &&
                      ingestPreview.files.length > 0 && (
                        <ScrollArea className="h-24 rounded-md border border-border/60">
                          <ul className="p-1.5 space-y-0.5">
                            {ingestPreview.files.map((f) => (
                              <li
                                key={f}
                                className="truncate font-mono text-[11px] text-muted-foreground"
                              >
                                {f}
                              </li>
                            ))}
                          </ul>
                        </ScrollArea>
                      )}
                  </div>
                )}
                {/* Path editável — Enter ou botão "Ir" navega para o destino.
            Backend recusa (403) se o usuário comum sair das pastas seguras. */}
                <div className="flex items-center gap-1.5">
                  <Input
                    value={pathInput}
                    onChange={(e) => setPathInput(e.target.value)}
                    autoComplete="off"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleGo();
                      }
                    }}
                    placeholder={m.workspace_path_placeholder()}
                    spellCheck={false}
                    className="h-8 text-xs font-mono"
                    disabled={loading || offline}
                    data-testid="workspace-path-input"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2"
                    onClick={handleGo}
                    disabled={loading || offline || !pathInput.trim()}
                    title={
                      offline ? m.network_disabled_offline() : m.workspace_go()
                    }
                    data-testid="workspace-go-btn"
                  >
                    <CornerDownLeft className="w-3.5 h-3.5" />
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2"
                    onClick={handleReload}
                    disabled={
                      loading ||
                      offline ||
                      (!listing &&
                        !lastLoadedPathRef.current &&
                        !pathInput.trim())
                    }
                    title={
                      offline
                        ? m.network_disabled_offline()
                        : m.workspace_reload()
                    }
                  >
                    <RefreshCw
                      className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
                    />
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2"
                    onClick={() => setCreatingFolder((v) => !v)}
                    disabled={loading || offline || !listing}
                    title={
                      offline
                        ? m.network_disabled_offline()
                        : m.workspace_new_folder()
                    }
                    data-testid="workspace-new-folder-btn"
                  >
                    <FolderPlus className="w-3.5 h-3.5" />
                  </Button>
                </div>

                {creatingFolder && (
                  <div className="flex items-center gap-1.5">
                    <Input
                      value={newFolderName}
                      onChange={(e) => setNewFolderName(e.target.value)}
                      autoComplete="off"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          void handleCreateFolder();
                        } else if (e.key === "Escape") {
                          setCreatingFolder(false);
                          setNewFolderName("");
                        }
                      }}
                      placeholder={m.workspace_new_folder_placeholder()}
                      spellCheck={false}
                      className="h-8 text-xs"
                      disabled={folderSubmitting}
                      data-testid="workspace-new-folder-input"
                    />
                    <Button
                      type="button"
                      size="sm"
                      className="h-8 px-2.5 text-xs"
                      onClick={() => void handleCreateFolder()}
                      disabled={folderSubmitting || !newFolderName.trim()}
                      data-testid="workspace-new-folder-create-btn"
                    >
                      {folderSubmitting ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        m.workspace_new_folder_create()
                      )}
                    </Button>
                  </div>
                )}

                {error && (
                  <div className="text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded-md px-3 py-2">
                    {error}
                  </div>
                )}

                {/* Directory browser — altura fixa deliberada (não
                    redimensionável pelo usuário): só reduzida de h-64 pra
                    h-40, que antes forçava scroll pra alcançar o botão
                    "Indexar esta pasta" logo abaixo, mesmo com poucas
                    entradas na listagem. */}
                <ScrollArea className="h-40 rounded-md border border-border">
                  <div className="p-1">
                    {listing?.parent && (
                      <button
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent text-left transition-colors"
                        onClick={() => load(listing.parent!)}
                        disabled={loading}
                      >
                        <ChevronLeft className="w-4 h-4 shrink-0 text-muted-foreground" />
                        {m.workspace_browse_up()}
                      </button>
                    )}

                    {/* Atalho explícito para a tela de discos, oculto quando já
                    está nela. Útil quando o usuário pulou direto para uma
                    pasta via texto e quer ver outras unidades. */}
                    {listing && !listing.at_drives_root && (
                      <button
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent text-left transition-colors text-muted-foreground"
                        onClick={() => load(DRIVES_PSEUDO_PATH)}
                        disabled={loading}
                      >
                        <HardDrive className="w-4 h-4 shrink-0" />
                        {m.workspace_browse_drives()}
                      </button>
                    )}

                    {listing && listing.entries.length === 0 && (
                      <p className="px-3 py-4 text-sm text-muted-foreground text-center">
                        {m.workspace_browse_empty()}
                      </p>
                    )}

                    {listing?.entries.map((entry) => {
                      const isDrive = entry.kind === "drive";
                      const Icon = isDrive ? HardDrive : Folder;
                      return (
                        <button
                          key={entry.path}
                          className="w-full flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent text-left transition-colors"
                          onClick={() => load(entry.path)}
                          disabled={loading}
                        >
                          <Icon
                            className={`w-4 h-4 shrink-0 ${isDrive ? "text-sky-500" : "text-muted-foreground"}`}
                          />
                          <span className="truncate font-medium">
                            {entry.name}
                          </span>
                          {entry.label && (
                            <span className="truncate text-xs text-muted-foreground">
                              {entry.label}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </ScrollArea>

                {mode === "trust" && (
                  <label className="flex items-center gap-2 text-sm text-foreground/80 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={gitInit}
                      onChange={(e) => setGitInit(e.target.checked)}
                      className="rounded border-border"
                      data-testid="workspace-git-init-checkbox"
                    />
                    <GitBranch className="w-4 h-4 shrink-0 text-muted-foreground" />
                    {m.workspace_git_init_label()}
                  </label>
                )}
              </>
            )}

            {tab === "ssh" && (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">
                    {m.workspace_ssh_host()}
                  </label>
                  <Input
                    value={sshHost}
                    onChange={(e) => setSshHost(e.target.value)}
                    placeholder={m.workspace_ssh_host_placeholder()}
                    className="h-8 text-xs font-mono"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">
                    {m.workspace_ssh_path()}
                  </label>
                  <Input
                    value={sshPath}
                    onChange={(e) => setSshPath(e.target.value)}
                    placeholder={m.workspace_ssh_path_placeholder()}
                    className="h-8 text-xs font-mono"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">
                    {m.workspace_ssh_key()}
                  </label>
                  <div className="flex items-center gap-1.5">
                    <Select
                      value={sshKeyId || "__none__"}
                      onValueChange={(v) =>
                        setSshKeyId(v === "__none__" ? "" : v)
                      }
                    >
                      <SelectTrigger className="h-8 text-xs">
                        <SelectValue
                          placeholder={m.workspace_ssh_key_placeholder()}
                        />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__none__">
                          {m.workspace_ssh_key_none()}
                        </SelectItem>
                        {sshKeys.map((id) => (
                          <SelectItem key={id} value={id}>
                            {id}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <label className="inline-flex items-center gap-1 px-2 h-8 rounded-md border border-border/60 text-xs cursor-pointer hover:bg-muted/50">
                      <Upload className="w-3.5 h-3.5" />
                      <input
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) void handleUploadKey(f);
                        }}
                      />
                    </label>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={handleTestSsh}
                    disabled={sshTesting || offline || !sshHost.trim()}
                    title={offline ? m.network_disabled_offline() : undefined}
                    className="h-8"
                  >
                    {sshTesting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      m.workspace_ssh_test()
                    )}
                  </Button>
                  {sshTestResult && (
                    <span
                      className={`text-xs ${
                        sshTestResult.ok
                          ? "text-emerald-500"
                          : "text-destructive"
                      }`}
                    >
                      {sshTestResult.ok
                        ? m.workspace_ssh_ok()
                        : sshTestResult.message}
                    </span>
                  )}
                </div>
              </div>
            )}

            {tab === "codespace" && (
              <div className="space-y-3">
                {codespaceLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    {m.workspace_codespaces_loading()}
                  </div>
                ) : !codespaceAvailable ? (
                  <div className="text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded-md px-3 py-2">
                    {codespaceMessage || m.workspace_codespaces_unavailable()}
                  </div>
                ) : codespaces.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    {m.workspace_codespaces_empty()}
                  </p>
                ) : (
                  <ScrollArea className="h-64 rounded-md border border-border">
                    <div className="p-1 divide-y divide-border/60">
                      {codespaces.map((cs) => (
                        <button
                          key={cs.name}
                          onClick={() => void handleConfirmCodespace(cs)}
                          disabled={submitting || offline}
                          title={
                            offline ? m.network_disabled_offline() : undefined
                          }
                          className="w-full text-left px-3 py-2 hover:bg-accent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <div className="text-sm font-medium text-foreground truncate">
                            {cs.repository || cs.name}
                          </div>
                          <div className="text-[11px] text-muted-foreground font-mono truncate">
                            {cs.name} · {cs.state}
                          </div>
                        </button>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>
            )}

            <DialogFooter>
              <Button
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={submitting}
              >
                {m.workspace_cancel()}
              </Button>
              {tab === "local" && (
                <Button
                  onClick={handleConfirm}
                  disabled={
                    !listing ||
                    submitting ||
                    folderSubmitting ||
                    loading ||
                    offline
                  }
                  title={offline ? m.network_disabled_offline() : undefined}
                  data-testid="workspace-trust-confirm-btn"
                >
                  {mode === "ingest"
                    ? m.workspace_ingest_confirm()
                    : m.workspace_trust_confirm()}
                </Button>
              )}
              {tab === "ssh" && (
                <Button
                  onClick={handleConfirmSsh}
                  disabled={submitting || offline || !sshHost.trim()}
                  title={offline ? m.network_disabled_offline() : undefined}
                >
                  {m.workspace_ssh_confirm()}
                </Button>
              )}
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

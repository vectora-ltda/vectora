"use client";

/**
 * RagSettingsPanel — gear + painel de configuração do RAG na aba de memória.
 *
 * Controla (persistido no backend via /rag/settings): reranker on/off + top_k,
 * providers de rerank/embedding, tipos de arquivo a ingerir. Lista as coleções
 * (/rag/collections) com botão de apagar. Caso de uso paralelo ao Context Graph:
 * usar o RAG para código enquanto o grafo cuida dos markdowns.
 *
 * `useRagSettings` centraliza o estado; `RagSettingsButton` (gatilho inline,
 * ex.: ao lado da busca) e `RagSettingsSlidePanel` (conteúdo modal)
 * são exportados separados para o consumidor controlar onde cada um entra no
 * layout — o painel precisa ocupar a largura total da workbench numa linha
 * própria abaixo do gatilho, nunca dividir espaço com ele na mesma linha
 * flex (senão o conteúdo do formulário força a linha a estourar a largura).
 * `RagSettingsPanel` compõe os dois com o hook embutido, para uso standalone.
 */

import { useCallback, useEffect, useState } from "react";
import { Search, Settings2, Trash2, RefreshCw } from "lucide-react";

import {
  ALL_GRAPH_FILE_TYPES,
  type GraphFileType,
} from "@/lib/stores/context-graph-settings-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { m } from "@/lib/paraglide/messages";

interface RagSettings {
  reranker_enabled: boolean;
  reranker_top_k: number;
  rerank_provider: string;
  embed_provider: string;
  embed_model: string;
  ingest_file_types: string[];
  //: Quais providers de rerank têm key/modelo configurados — vem de
  //: GET /rag/settings (backend/api/handlers/rag.py). "auto" nunca entra
  //: aqui: está sempre "disponível" (o backend escolhe o que der certo).
  rerank_provider_available?: Record<string, boolean>;
}

interface Collection {
  name: string;
  count: number | null;
}

const DEFAULTS: RagSettings = {
  reranker_enabled: true,
  reranker_top_k: 5,
  rerank_provider: "auto",
  embed_provider: "auto",
  embed_model: "",
  ingest_file_types: [],
  rerank_provider_available: {},
};

// Reranker: Cohere/Voyage (rerank nativo fixo) + OpenRouter (rerank via
// modelo configurado em Provider Routing — depende de key + modelo
// escolhido, ver rerank_provider_available). Embedding aceita os 4 —
// backend resolve em storage/factory.py::_build_lc_embeddings (lê
// embed_provider/embed_model deste mesmo /rag/settings).
const RERANK_PROVIDERS = ["auto", "cohere", "voyage", "openrouter"] as const;
const EMBED_PROVIDERS = [
  "auto",
  "cohere",
  "voyage",
  "ollama",
  "openrouter",
] as const;

const PROVIDER_LABELS: Record<string, () => string> = {
  auto: m.rag_provider_auto,
  cohere: m.rag_provider_cohere,
  voyage: m.rag_provider_voyage,
  ollama: m.rag_provider_ollama,
  openrouter: m.rag_provider_openrouter,
};

export function useRagSettings() {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<RagSettings>(DEFAULTS);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [settingsStatus, setSettingsStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [collectionsStatus, setCollectionsStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [patching, setPatching] = useState(false);

  const loadCollections = useCallback(async () => {
    // The hook owns this fetch lifecycle; this state update mirrors the request.
    // oxlint-disable-next-line react/set-state-in-effect
    setCollectionsStatus("loading");
    try {
      const res = await fetch("/rag/collections");
      if (!res.ok) {
        setCollectionsStatus("error");
        return;
      }
      const data = (await res.json()) as { collections?: Collection[] };
      setCollections(Array.isArray(data.collections) ? data.collections : []);
      setCollectionsStatus("ready");
    } catch {
      setCollectionsStatus("error");
    }
  }, []);

  useEffect(() => {
    // Collection hydration is an intentional external synchronization.
    // oxlint-disable-next-line react/set-state-in-effect
    void loadCollections();
  }, [loadCollections]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    // This effect starts the remote load when the panel opens; the loading
    // state is intentionally synchronized before the request begins.
    // oxlint-disable-next-line react/set-state-in-effect
    setSettingsStatus("loading");
    void (async () => {
      try {
        const res = await fetch("/rag/settings");
        if (res.ok && alive) {
          const data = (await res.json()) as Partial<RagSettings>;
          setSettings({ ...DEFAULTS, ...data });
          setSettingsStatus("ready");
        } else if (alive) {
          setSettingsStatus("error");
        }
      } catch {
        if (alive) setSettingsStatus("error");
      }
    })();
    return () => {
      alive = false;
    };
  }, [open]);

  const patch = useCallback(
    async (changes: Partial<RagSettings>) => {
      const previous = settings;
      setSettings((s) => ({ ...s, ...changes }));
      setPatching(true);
      try {
        const res = await fetch("/rag/settings", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(changes),
        });
        if (res.ok) {
          const data = (await res.json()) as RagSettings;
          setSettings({ ...DEFAULTS, ...data });
          return;
        }
        setSettings(previous);
        useToastStore.getState().error(m.workbench_settings_error());
      } catch {
        setSettings(previous);
        useToastStore.getState().error(m.workbench_settings_error());
      } finally {
        setPatching(false);
      }
    },
    [settings],
  );

  const deleteCollection = useCallback(async (name: string) => {
    try {
      const res = await fetch(`/rag/collections/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setCollections((c) => c.filter((x) => x.name !== name));
      } else {
        useToastStore.getState().error(m.rag_collection_delete());
      }
    } catch {
      useToastStore.getState().error(m.rag_collection_delete());
    }
  }, []);

  return {
    open,
    toggle: () => setOpen((v) => !v),
    close: () => setOpen(false),
    settings,
    settingsStatus,
    collectionsStatus,
    patching,
    collections,
    patch,
    loadCollections,
    deleteCollection,
  };
}

type RagSettingsState = ReturnType<typeof useRagSettings>;

export function RagSettingsButton({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      aria-label={m.rag_settings_title()}
      title={m.rag_settings_title()}
      aria-expanded={open}
      data-testid="rag-settings-btn"
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted/50 hover:text-foreground"
    >
      <Settings2 className="h-3.5 w-3.5" />
    </button>
  );
}

/** Ações das coleções ficam fora do conteúdo persistente de configurações. */
export function RagCollectionsSection({
  collections,
  collectionsStatus,
  loadCollections,
  deleteCollection,
}: Pick<
  RagSettingsState,
  "collections" | "collectionsStatus" | "loadCollections" | "deleteCollection"
>) {
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  return (
    <section className="border-t border-border/60 pt-2">
      <div className="flex items-center justify-between">
        <p className="font-medium text-foreground">
          {m.rag_collections_title()}
        </p>
        <button
          type="button"
          onClick={() => void loadCollections()}
          aria-label={m.workbench_files_refresh()}
          className="text-muted-foreground hover:text-foreground"
        >
          <RefreshCw className="h-3 w-3" />
        </button>
      </div>
      {collectionsStatus === "loading" ? (
        <p className="mt-1 text-[10px] text-muted-foreground">
          {m.workbench_settings_loading()}
        </p>
      ) : collectionsStatus === "error" ? (
        <p className="mt-1 text-[10px] text-destructive">
          {m.workbench_settings_error()}
        </p>
      ) : collections.length === 0 ? (
        <p className="mt-1 text-[10px] text-muted-foreground">
          {m.rag_collections_empty()}
        </p>
      ) : (
        <ul className="mt-1 space-y-1">
          {collections.map((collection) => (
            <li
              key={collection.name}
              className="flex items-center justify-between gap-2 rounded border border-border/60 px-2 py-1"
            >
              <span className="min-w-0 truncate text-foreground">
                {collection.name}
                {collection.count != null && (
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    {m.rag_collection_count({ n: collection.count })}
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={() => setPendingDelete(collection.name)}
                aria-label={m.rag_collection_delete()}
                title={m.rag_collection_delete()}
                className="shrink-0 text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        title={m.rag_collection_delete()}
        description={`${m.rag_collection_delete_confirm({ name: pendingDelete ?? "" })} ${m.workbench_settings_instance_warning()}`}
        confirmLabel={m.rag_collection_delete()}
        variant="destructive"
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          const name = pendingDelete;
          setPendingDelete(null);
          if (name) await deleteCollection(name);
        }}
      />
    </section>
  );
}

export function RagSettingsForm({
  open,
  close,
  settings,
  collections,
  patch,
  loadCollections,
  deleteCollection,
  settingsStatus,
  collectionsStatus,
  patching,
  showCollections = true,
}: Pick<
  RagSettingsState,
  | "open"
  | "close"
  | "settings"
  | "collections"
  | "patch"
  | "loadCollections"
  | "deleteCollection"
  | "settingsStatus"
  | "collectionsStatus"
  | "patching"
> & { showCollections?: boolean }) {
  if (settingsStatus === "loading") {
    return (
      <p className="p-4 text-xs text-muted-foreground">
        {m.workbench_settings_loading()}
      </p>
    );
  }
  if (settingsStatus === "error") {
    return (
      <p className="p-4 text-xs text-destructive">
        {m.workbench_settings_error()}
      </p>
    );
  }
  return (
    <div className="min-w-0 space-y-3 text-xs" aria-busy={patching}>
      {/* Reranker on/off + top_k */}
      <label className="flex items-center justify-between gap-2 cursor-pointer select-none">
        <span className="text-foreground">{m.rag_reranker_enabled()}</span>
        <input
          type="checkbox"
          checked={settings.reranker_enabled}
          onChange={(e) => void patch({ reranker_enabled: e.target.checked })}
          className="accent-[var(--color-primary)]"
        />
      </label>
      <p className="-mt-1.5 text-[10px] text-muted-foreground">
        {m.rag_reranker_help()}
      </p>
      <label className="flex items-center justify-between gap-2">
        <span className="text-foreground">{m.rag_reranker_top_k()}</span>
        <input
          type="number"
          min={1}
          max={50}
          value={settings.reranker_top_k}
          disabled={!settings.reranker_enabled}
          onChange={(e) =>
            void patch({
              reranker_top_k: Math.max(1, Number(e.target.value) || 1),
            })
          }
          className="w-16 bg-background border border-border/60 rounded px-1.5 py-0.5 text-right disabled:opacity-40"
        />
      </label>

      {/* Providers */}
      <label className="flex items-center justify-between gap-2">
        <span className="text-foreground">{m.rag_rerank_provider()}</span>
        <ProviderSelect
          value={settings.rerank_provider}
          onChange={(v) => void patch({ rerank_provider: v })}
          providers={RERANK_PROVIDERS}
          unavailable={
            new Set(
              Object.entries(settings.rerank_provider_available ?? {})
                .filter(([, available]) => !available)
                .map(([provider]) => provider),
            )
          }
        />
      </label>
      {settings.rerank_provider !== "auto" &&
        settings.rerank_provider_available?.[settings.rerank_provider] ===
          false && (
          <p className="text-[10px] text-amber-600 dark:text-amber-400">
            {m.rag_rerank_provider_unavailable_warning()}
          </p>
        )}
      <label className="flex items-center justify-between gap-2">
        <span className="text-foreground">{m.rag_embed_provider()}</span>
        <ProviderSelect
          value={settings.embed_provider}
          onChange={(v) => void patch({ embed_provider: v, embed_model: "" })}
          providers={EMBED_PROVIDERS}
        />
      </label>
      {(settings.embed_provider === "ollama" ||
        settings.embed_provider === "openrouter") && (
        <EmbedModelPicker
          provider={settings.embed_provider}
          value={settings.embed_model}
          onChange={(v) => void patch({ embed_model: v })}
        />
      )}

      {/* Tipos de arquivo a ingerir */}
      <div>
        <p className="font-medium text-foreground">
          {m.graph_settings_filetypes()}
        </p>
        <div className="mt-1 space-y-1">
          {ALL_GRAPH_FILE_TYPES.map((t: GraphFileType) => (
            <label
              key={t}
              className="flex items-center gap-2 cursor-pointer select-none"
            >
              <input
                type="checkbox"
                checked={
                  settings.ingest_file_types.length === 0 ||
                  settings.ingest_file_types.includes(t)
                }
                onChange={() => {
                  const cur =
                    settings.ingest_file_types.length === 0
                      ? [...ALL_GRAPH_FILE_TYPES]
                      : settings.ingest_file_types;
                  const next = cur.includes(t)
                    ? cur.filter((x) => x !== t)
                    : [...cur, t];
                  void patch({ ingest_file_types: next });
                }}
                className="accent-[var(--color-primary)]"
              />
              <span className="text-foreground">
                {t === "code"
                  ? m.graph_filetype_code()
                  : t === "document"
                    ? m.graph_filetype_document()
                    : m.graph_filetype_paper()}
              </span>
            </label>
          ))}
        </div>
      </div>

      {showCollections ? (
        <RagCollectionsSection
          collections={collections}
          collectionsStatus={collectionsStatus}
          loadCollections={loadCollections}
          deleteCollection={deleteCollection}
        />
      ) : null}
    </div>
  );
}

export function RagSettingsSlidePanel(
  props: Pick<
    RagSettingsState,
    | "open"
    | "close"
    | "settings"
    | "collections"
    | "patch"
    | "loadCollections"
    | "deleteCollection"
  >,
) {
  const formProps = {
    ...props,
    settingsStatus: "ready" as const,
    collectionsStatus: "ready" as const,
    patching: false,
  };
  return (
    <WorkbenchDialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open) props.close();
      }}
      title={m.rag_settings_title()}
      description={m.rag_settings_title()}
      testId="rag-settings-panel"
    >
      <RagSettingsForm {...formProps} />
    </WorkbenchDialog>
  );
}

/** Composição standalone (botão + painel juntos) — para uso fora de layouts
 * que precisem posicionar os dois em linhas separadas (ver `MemoryTab`). */
export function RagSettingsPanel() {
  const state = useRagSettings();
  return (
    <>
      <RagSettingsButton open={state.open} onToggle={state.toggle} />
      <RagSettingsSlidePanel {...state} />
    </>
  );
}

function ProviderSelect({
  value,
  onChange,
  providers,
  unavailable,
}: {
  value: string;
  onChange: (v: string) => void;
  providers: readonly string[];
  //: Providers sem key/config — ficam no dropdown mas desabilitados, com
  //: sufixo "(sem chave)". Escolher um provider que sabidamente não vai
  //: funcionar é pior que impedir a escolha.
  unavailable?: Set<string>;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-background border border-border/60 rounded px-1.5 py-0.5 text-foreground"
    >
      {providers.map((p) => (
        <option key={p} value={p} disabled={unavailable?.has(p)}>
          {PROVIDER_LABELS[p]()}
          {unavailable?.has(p) ? ` ${m.rag_provider_no_key_suffix()}` : ""}
        </option>
      ))}
    </select>
  );
}

interface DiscoveredModel {
  id: string;
  label: string;
}

/** Seletor de modelo pro provider de embedding quando é Ollama/OpenRouter —
 * nenhum dos dois tem um default sensato (ao contrário de Cohere/Voyage, que
 * já têm modelo fixo em settings.py), então o usuário escolhe explicitamente
 * a partir de uma lista descoberta, nunca texto livre (erro de digitação
 * viraria falha silenciosa de embedding — mesmo motivo que o seletor de
 * LLM em provider-routing-tab.tsx já evita texto livre). */
function EmbedModelPicker({
  provider,
  value,
  onChange,
}: {
  provider: "ollama" | "openrouter";
  value: string;
  onChange: (v: string) => void;
}) {
  const [models, setModels] = useState<DiscoveredModel[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  const loadOllama = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/provider-routing/ollama/models");
      if (!res.ok) return;
      const data = (await res.json()) as {
        reachable: boolean;
        models: { name: string }[];
      };
      setModels(data.models.map((mo) => ({ id: mo.name, label: mo.name })));
    } catch {
      /* Ollama fora do ar — lista fica vazia, sem quebrar o painel */
    } finally {
      setLoading(false);
    }
  }, []);

  const searchOpenRouter = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const res = await fetch(
        `/provider-routing/openrouter/models${q ? `?q=${encodeURIComponent(q)}` : ""}`,
      );
      if (!res.ok) return;
      const data = (await res.json()) as {
        models: { id: string; name: string }[];
      };
      setModels(data.models.map((mo) => ({ id: mo.id, label: mo.name })));
    } catch {
      /* offline: mantém a lista já carregada */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Reinicia a lista antes de rebuscar modelos no backend (rede) ao trocar de provider.
    // oxlint-disable-next-line react/set-state-in-effect
    setModels([]);
    if (provider === "ollama") void loadOllama();
    else void searchOpenRouter("");
  }, [provider, loadOllama, searchOpenRouter]);

  return (
    <div className="space-y-1">
      {provider === "openrouter" && (
        <div className="flex items-center gap-1">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void searchOpenRouter(query);
            }}
            placeholder={m.rag_embed_model_search_placeholder()}
            className="flex-1 bg-background border border-border/60 rounded px-1.5 py-0.5 text-foreground"
          />
          <button
            onClick={() => void searchOpenRouter(query)}
            aria-label={m.rag_embed_model_search_placeholder()}
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            <Search className="h-3 w-3" />
          </button>
        </div>
      )}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading}
        className="w-full bg-background border border-border/60 rounded px-1.5 py-0.5 text-foreground disabled:opacity-40"
      >
        <option value="">{m.rag_embed_model_select_placeholder()}</option>
        {models.map((mo) => (
          <option key={mo.id} value={mo.id}>
            {mo.label}
          </option>
        ))}
      </select>
    </div>
  );
}

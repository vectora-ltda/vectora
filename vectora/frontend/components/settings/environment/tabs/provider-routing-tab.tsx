"use client";

/**
 * ProviderRoutingTab — modelos de LLM locais/dinâmicos (Ollama, OpenRouter).
 *
 * Ollama: descoberta via GET /provider-routing/ollama/models (consulta
 * {base_url}/api/tags do host configurado — nunca digitação livre de nome de
 * modelo, evita erro de digitação virar falha silenciosa no chat).
 *
 * OpenRouter: exige API key (validada contra /auth/key antes de persistir via
 * POST /provider-routing/openrouter/key), depois permite buscar no catálogo público
 * (GET /provider-routing/openrouter/models?q=, cacheado ~1h no backend) e registrar
 * os modelos desejados.
 *
 * Em ambos os casos, modelos registrados (POST .../registered) aparecem no
 * ModelSelector do composer (GET /models/providers agrega o catálogo
 * estático com os registrados de cada gateway).
 */

import { Loader2, Plus, RefreshCw, Search, Server, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { m } from "@/lib/paraglide/messages";

interface OllamaModelInfo {
  name: string;
  size: number | null;
  modified_at: string | null;
}

interface RegisteredModel {
  id: string;
  tag: string;
  created_at: string;
}

interface OpenRouterStatus {
  configured: boolean;
  masked: string;
}

interface OpenRouterModelInfo {
  id: string;
  name: string;
  context_length: number | null;
}

interface NineRouterStatus {
  configured: boolean;
  base_url: string | null;
  masked: string;
}

interface NineRouterModelInfo {
  id: string;
  name: string;
}

interface LlamaCppModelInfo {
  id: string;
  name: string;
}

interface LlamaCppReleaseAsset {
  name: string;
  url: string;
  size: number;
}

async function discoverModels(): Promise<{
  reachable: boolean;
  models: OllamaModelInfo[];
}> {
  const res = await fetch("/provider-routing/ollama/models");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function discoverLlamaCppModels(): Promise<{
  reachable: boolean;
  models: LlamaCppModelInfo[];
}> {
  const res = await fetch("/provider-routing/llamacpp/models");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function testLlamaCppConnection(): Promise<{
  status: string;
  models?: LlamaCppModelInfo[];
}> {
  const res = await fetch("/provider-routing/llamacpp/test", {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function fetchLlamaCppReleases(): Promise<LlamaCppReleaseAsset[]> {
  const res = await fetch("/provider-routing/llamacpp/releases");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as {
    releases?: Array<{ assets?: LlamaCppReleaseAsset[] }>;
  };
  return data.releases?.flatMap((release) => release.assets ?? []) ?? [];
}

async function installLlamaCppRuntime(assetUrl: string): Promise<void> {
  const res = await fetch("/provider-routing/llamacpp/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_url: assetUrl }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function searchHuggingFaceModels(
  query: string,
  provider: "ollama" | "llamacpp" = "llamacpp",
): Promise<LlamaCppModelInfo[]> {
  const res = await fetch(
    `/provider-routing/huggingface/models?q=${encodeURIComponent(query)}&provider=${provider}`,
  );
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { models?: LlamaCppModelInfo[] };
  return data.models ?? [];
}

async function downloadHuggingFaceModel(repoId: string): Promise<string> {
  const metadataResponse = await fetch(
    `/provider-routing/huggingface/models/${repoId}`,
  );
  if (!metadataResponse.ok) throw new Error(`Erro ${metadataResponse.status}`);
  const metadata = (await metadataResponse.json()) as {
    files?: Array<{ rfilename: string; format?: string }>;
  };
  const file = metadata.files?.find((item) => item.format === "GGUF");
  if (!file) throw new Error("Nenhum arquivo GGUF encontrado");
  const response = await fetch("/provider-routing/huggingface/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_id: repoId, filename: file.rfilename }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  const result = (await response.json()) as { path: string };
  return result.path;
}

function HuggingFaceCatalogSection() {
  const [provider, setProvider] = useState<"ollama" | "llamacpp">("llamacpp");
  const [query, setQuery] = useState("llama");
  const [models, setModels] = useState<LlamaCppModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    setLoading(true);
    setError(null);
    try {
      setModels(await searchHuggingFaceModels(query, provider));
    } catch {
      setError("Erro ao consultar a Hugging Face");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="font-medium">{m.provider_routing_hf_title()}</h3>
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_hf_subtitle()}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label={m.provider_routing_hf_query_placeholder()}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={m.provider_routing_hf_query_placeholder()}
          className="min-w-48 flex-1"
        />
        <select
          aria-label={m.provider_routing_hf_runtime_label()}
          className="rounded-md border bg-background px-3 text-sm"
          value={provider}
          onChange={(event) =>
            setProvider(event.target.value as "ollama" | "llamacpp")
          }
        >
          <option value="llamacpp">
            {m.provider_routing_llamacpp_title()}
          </option>
          <option value="ollama">{m.provider_routing_ollama_title()}</option>
        </select>
        <Button type="button" onClick={() => void search()} disabled={loading}>
          <Search className="mr-2 size-4" /> {m.provider_routing_hf_search()}
        </Button>
      </div>
      {models.length > 0 && (
        <div className="space-y-1 text-sm">
          {models.map((model) => (
            <div
              key={model.id}
              className="flex items-center justify-between gap-2"
            >
              <span className="truncate">{model.id}</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={downloading !== null}
                onClick={() => {
                  setDownloading(model.id);
                  setError(null);
                  void downloadHuggingFaceModel(model.id)
                    .then((path) => setDownloaded(path))
                    .catch(() =>
                      setError(m.provider_routing_hf_download_error()),
                    )
                    .finally(() => setDownloading(null));
                }}
              >
                {downloading === model.id
                  ? m.provider_routing_hf_downloading()
                  : m.provider_routing_hf_download()}
              </Button>
            </div>
          ))}
        </div>
      )}
      {downloaded && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_hf_downloaded({ path: downloaded })}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

type Gateway = "ollama" | "openrouter" | "nine-router" | "llamacpp";

async function fetchRegistered(gateway: Gateway): Promise<RegisteredModel[]> {
  const res = await fetch(`/provider-routing/${gateway}/registered`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function registerModel(gateway: Gateway, tag: string): Promise<void> {
  const res = await fetch(`/provider-routing/${gateway}/registered`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function unregisterModel(gateway: Gateway, id: string): Promise<void> {
  const res = await fetch(`/provider-routing/${gateway}/registered/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function fetchOpenRouterStatus(): Promise<OpenRouterStatus> {
  const res = await fetch("/provider-routing/openrouter/status");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function saveOpenRouterKey(apiKey: string): Promise<OpenRouterStatus> {
  const res = await fetch("/provider-routing/openrouter/key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "" }));
    throw new Error(body.detail || `Erro ${res.status}`);
  }
  return res.json();
}

async function removeOpenRouterKey(): Promise<void> {
  const res = await fetch("/provider-routing/openrouter/key", {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function searchOpenRouterCatalog(
  q: string,
): Promise<OpenRouterModelInfo[]> {
  const res = await fetch(
    `/provider-routing/openrouter/models${q ? `?q=${encodeURIComponent(q)}` : ""}`,
  );
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = await res.json();
  return data.models;
}

async function fetchNineRouterStatus(): Promise<NineRouterStatus> {
  const res = await fetch("/provider-routing/nine-router/status");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function saveNineRouterConfig(
  baseUrl: string,
  apiKey: string,
): Promise<NineRouterStatus> {
  const res = await fetch("/provider-routing/nine-router/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "" }));
    throw new Error(body.detail || `Erro ${res.status}`);
  }
  return res.json();
}

async function removeNineRouterConfig(): Promise<void> {
  const res = await fetch("/provider-routing/nine-router/config", {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function discoverNineRouterModels(): Promise<{
  reachable: boolean;
  models: NineRouterModelInfo[];
}> {
  const res = await fetch("/provider-routing/nine-router/models");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

function RegisteredModelsList({
  registered,
  loading,
  removingId,
  onRemove,
}: {
  registered: RegisteredModel[];
  loading: boolean;
  removingId: string | null;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="space-y-2 pt-2 border-t">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        {m.provider_routing_registered_title()}
      </p>
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      ) : registered.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_registered_empty()}
        </p>
      ) : (
        <div className="space-y-1.5">
          {registered.map((model) => (
            <div
              key={model.id}
              className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2"
            >
              <span className="text-sm font-mono truncate">{model.tag}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive shrink-0"
                onClick={() => onRemove(model.id)}
                disabled={removingId === model.id}
              >
                {removingId === model.id ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Trash2 className="w-3.5 h-3.5" />
                )}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OllamaSection() {
  const [registered, setRegistered] = useState<RegisteredModel[]>([]);
  const [loadingRegistered, setLoadingRegistered] = useState(true);
  const [discovered, setDiscovered] = useState<OllamaModelInfo[] | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [registeringTag, setRegisteringTag] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadRegistered = useCallback(async () => {
    setLoadingRegistered(true);
    try {
      setRegistered(await fetchRegistered("ollama"));
    } catch {
      setError(m.provider_routing_error_load());
    } finally {
      setLoadingRegistered(false);
    }
  }, []);

  useEffect(() => {
    // Busca de dados ao montar (I/O de rede), não estado derivado de prop —
    // uso correto de efeito, ver justificativa em use-context-graph.ts.
    // oxlint-disable-next-line react/set-state-in-effect
    void loadRegistered();
  }, [loadRegistered]);

  const handleDiscover = async () => {
    setDiscovering(true);
    setError(null);
    try {
      const data = await discoverModels();
      setReachable(data.reachable);
      setDiscovered(data.models);
    } catch {
      setReachable(false);
      setDiscovered([]);
      setError(m.provider_routing_error_discover());
    } finally {
      setDiscovering(false);
    }
  };

  const handleRegister = async (tag: string) => {
    setRegisteringTag(tag);
    setError(null);
    try {
      await registerModel("ollama", tag);
      await loadRegistered();
    } catch {
      setError(m.provider_routing_error_register());
    } finally {
      setRegisteringTag(null);
    }
  };

  const handleRemove = async (id: string) => {
    setRemovingId(id);
    setError(null);
    try {
      await unregisterModel("ollama", id);
      setRegistered((prev) => prev.filter((model) => model.id !== id));
    } catch {
      setError(m.provider_routing_error_remove());
    } finally {
      setRemovingId(null);
    }
  };

  const registeredTags = new Set(registered.map((model) => model.tag));

  return (
    <div className="space-y-4">
      <div className="space-y-0.5">
        <p className="text-sm font-medium flex items-center gap-1.5">
          <Server className="w-3.5 h-3.5 text-muted-foreground" />
          {m.provider_routing_ollama_title()}
        </p>
        <p className="text-xs text-muted-foreground max-w-[360px]">
          {m.provider_routing_ollama_subtitle()}
        </p>
      </div>

      {error && (
        <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">
          {error}
        </p>
      )}

      {/* Descoberta */}
      <div className="space-y-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void handleDiscover()}
          disabled={discovering}
        >
          {discovering ? (
            <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
          )}
          {m.provider_routing_detect_models()}
        </Button>

        {reachable === false && (
          <p className="text-xs text-muted-foreground">
            {m.provider_routing_ollama_unreachable()}
          </p>
        )}

        {discovered && discovered.length > 0 && (
          <div className="space-y-1.5">
            {discovered.map((model) => {
              const already = registeredTags.has(model.name);
              return (
                <div
                  key={model.name}
                  className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2"
                >
                  <span className="text-sm font-mono truncate">
                    {model.name}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs shrink-0"
                    onClick={() => void handleRegister(model.name)}
                    disabled={already || registeringTag === model.name}
                  >
                    {registeringTag === model.name ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : already ? (
                      m.provider_routing_already_registered()
                    ) : (
                      <>
                        <Plus className="w-3.5 h-3.5 mr-1" />
                        {m.provider_routing_register()}
                      </>
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <RegisteredModelsList
        registered={registered}
        loading={loadingRegistered}
        removingId={removingId}
        onRemove={(id) => void handleRemove(id)}
      />
    </div>
  );
}

function OpenRouterSection() {
  const [status, setStatus] = useState<OpenRouterStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [keyInput, setKeyInput] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [removingKey, setRemovingKey] = useState(false);
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<OpenRouterModelInfo[]>([]);
  const [searching, setSearching] = useState(false);
  const [registered, setRegistered] = useState<RegisteredModel[]>([]);
  const [loadingRegistered, setLoadingRegistered] = useState(true);
  const [registeringId, setRegisteringId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      setStatus(await fetchOpenRouterStatus());
    } catch {
      setError(m.provider_routing_openrouter_error_status());
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadRegistered = useCallback(async () => {
    setLoadingRegistered(true);
    try {
      setRegistered(await fetchRegistered("openrouter"));
    } catch {
      setError(m.provider_routing_error_load());
    } finally {
      setLoadingRegistered(false);
    }
  }, []);

  useEffect(() => {
    // Busca de dados ao montar (I/O de rede), não estado derivado de prop —
    // uso correto de efeito, ver justificativa em use-context-graph.ts.
    // oxlint-disable-next-line react/set-state-in-effect
    void loadStatus();
    void loadRegistered();
  }, [loadStatus, loadRegistered]);

  // Busca com debounce — catálogo é cacheado no backend (~1h), seguro
  // consultar a cada pausa de digitação em vez de exigir um botão.
  useEffect(() => {
    if (!status?.configured) return;
    const handle = setTimeout(() => {
      setSearching(true);
      searchOpenRouterCatalog(query)
        .then(setCatalog)
        .catch(() => setError(m.provider_routing_openrouter_error_catalog()))
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(handle);
  }, [query, status?.configured]);

  const handleSaveKey = async () => {
    if (!keyInput.trim()) return;
    setSavingKey(true);
    setError(null);
    try {
      setStatus(await saveOpenRouterKey(keyInput.trim()));
      setKeyInput("");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : m.provider_routing_openrouter_error_key_save(),
      );
    } finally {
      setSavingKey(false);
    }
  };

  const handleRemoveKey = async () => {
    setRemovingKey(true);
    setError(null);
    try {
      await removeOpenRouterKey();
      setStatus({ configured: false, masked: "" });
      setCatalog([]);
    } catch {
      setError(m.provider_routing_openrouter_error_key_remove());
    } finally {
      setRemovingKey(false);
    }
  };

  const handleRegister = async (id: string) => {
    setRegisteringId(id);
    setError(null);
    try {
      await registerModel("openrouter", id);
      await loadRegistered();
    } catch {
      setError(m.provider_routing_error_register());
    } finally {
      setRegisteringId(null);
    }
  };

  const handleRemove = async (id: string) => {
    setRemovingId(id);
    setError(null);
    try {
      await unregisterModel("openrouter", id);
      setRegistered((prev) => prev.filter((model) => model.id !== id));
    } catch {
      setError(m.provider_routing_error_remove());
    } finally {
      setRemovingId(null);
    }
  };

  const registeredTags = new Set(registered.map((model) => model.tag));

  return (
    <div className="space-y-4 pt-4 border-t">
      <div className="space-y-0.5">
        <p className="text-sm font-medium flex items-center gap-1.5">
          <Server className="w-3.5 h-3.5 text-muted-foreground" />
          {m.provider_routing_openrouter_title()}
        </p>
        <p className="text-xs text-muted-foreground max-w-[360px]">
          {m.provider_routing_openrouter_subtitle()}
        </p>
      </div>

      {error && (
        <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">
          {error}
        </p>
      )}

      {/* Key */}
      {loadingStatus ? (
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      ) : status?.configured ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2">
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
              {m.provider_routing_openrouter_key_configured()}
            </span>
            <span className="font-mono text-muted-foreground">
              {status.masked}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive shrink-0"
            onClick={() => void handleRemoveKey()}
            disabled={removingKey}
          >
            {removingKey ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              m.provider_routing_openrouter_key_remove()
            )}
          </Button>
        </div>
      ) : (
        <div className="flex gap-1.5">
          <Input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={m.provider_routing_openrouter_key_placeholder()}
            className="h-8 text-xs font-mono flex-1"
            autoComplete="off"
          />
          <Button
            size="sm"
            className="h-8"
            onClick={() => void handleSaveKey()}
            disabled={savingKey || !keyInput.trim()}
          >
            {savingKey ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              m.provider_routing_openrouter_key_save()
            )}
          </Button>
        </div>
      )}

      {/* Catálogo */}
      {status?.configured && (
        <div className="space-y-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={m.provider_routing_openrouter_search_placeholder()}
              className="h-8 text-xs pl-8"
              autoComplete="off"
            />
            {searching && (
              <Loader2 className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 animate-spin text-muted-foreground" />
            )}
          </div>

          {catalog.length > 0 && (
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {catalog.map((model) => {
                const already = registeredTags.has(model.id);
                return (
                  <div
                    key={model.id}
                    className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2"
                  >
                    <span className="text-sm font-mono truncate">
                      {model.id}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs shrink-0"
                      onClick={() => void handleRegister(model.id)}
                      disabled={already || registeringId === model.id}
                    >
                      {registeringId === model.id ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : already ? (
                        m.provider_routing_already_registered()
                      ) : (
                        <>
                          <Plus className="w-3.5 h-3.5 mr-1" />
                          {m.provider_routing_register()}
                        </>
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <RegisteredModelsList
        registered={registered}
        loading={loadingRegistered}
        removingId={removingId}
        onRemove={(id) => void handleRemove(id)}
      />
    </div>
  );
}

function NineRouterSection() {
  const [status, setStatus] = useState<NineRouterStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [baseUrlInput, setBaseUrlInput] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [removingConfig, setRemovingConfig] = useState(false);
  const [discovered, setDiscovered] = useState<NineRouterModelInfo[] | null>(
    null,
  );
  const [modelQuery, setModelQuery] = useState("");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [registeringId, setRegisteringId] = useState<string | null>(null);
  const [registered, setRegistered] = useState<RegisteredModel[]>([]);
  const [loadingRegistered, setLoadingRegistered] = useState(true);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      setStatus(await fetchNineRouterStatus());
    } catch {
      setError(m.provider_routing_nine_router_error_status());
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadRegistered = useCallback(async () => {
    setLoadingRegistered(true);
    try {
      setRegistered(await fetchRegistered("nine-router"));
    } catch {
      setError(m.provider_routing_error_load());
    } finally {
      setLoadingRegistered(false);
    }
  }, []);

  useEffect(() => {
    // Busca de dados ao montar (I/O de rede), não estado derivado de prop —
    // uso correto de efeito, ver justificativa em use-context-graph.ts.
    // oxlint-disable-next-line react/set-state-in-effect
    void loadStatus();
    void loadRegistered();
  }, [loadStatus, loadRegistered]);

  // Detecção automática assim que o proxy está configurado — igual ao
  // OpenRouter, que busca no catálogo sem exigir clique em botão. Só roda
  // uma vez por configuração (discovered !== null trava o re-disparo); o
  // botão "Detectar modelos" continua disponível pra atualizar manualmente.
  useEffect(() => {
    if (!status?.configured || discovered !== null) return;
    // oxlint-disable-next-line react/set-state-in-effect
    setDiscovering(true);
    setError(null);
    discoverNineRouterModels()
      .then((data) => {
        setReachable(data.reachable);
        setDiscovered(data.models);
      })
      .catch(() => {
        setReachable(false);
        setDiscovered([]);
        setError(m.provider_routing_error_discover());
      })
      .finally(() => setDiscovering(false));
  }, [status?.configured, discovered]);

  const handleSaveConfig = async () => {
    if (!baseUrlInput.trim() || !keyInput.trim()) return;
    setSavingConfig(true);
    setError(null);
    try {
      setStatus(
        await saveNineRouterConfig(baseUrlInput.trim(), keyInput.trim()),
      );
      setBaseUrlInput("");
      setKeyInput("");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : m.provider_routing_nine_router_error_config_save(),
      );
    } finally {
      setSavingConfig(false);
    }
  };

  const handleRemoveConfig = async () => {
    setRemovingConfig(true);
    setError(null);
    try {
      await removeNineRouterConfig();
      setStatus({ configured: false, base_url: null, masked: "" });
      setDiscovered(null);
      setReachable(null);
    } catch {
      setError(m.provider_routing_nine_router_error_config_remove());
    } finally {
      setRemovingConfig(false);
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    setError(null);
    setModelQuery("");
    try {
      const data = await discoverNineRouterModels();
      setReachable(data.reachable);
      setDiscovered(data.models);
    } catch {
      setReachable(false);
      setDiscovered([]);
      setError(m.provider_routing_error_discover());
    } finally {
      setDiscovering(false);
    }
  };

  const handleRegister = async (id: string) => {
    setRegisteringId(id);
    setError(null);
    try {
      await registerModel("nine-router", id);
      await loadRegistered();
    } catch {
      setError(m.provider_routing_error_register());
    } finally {
      setRegisteringId(null);
    }
  };

  const handleRemove = async (id: string) => {
    setRemovingId(id);
    setError(null);
    try {
      await unregisterModel("nine-router", id);
      setRegistered((prev) => prev.filter((model) => model.id !== id));
    } catch {
      setError(m.provider_routing_error_remove());
    } finally {
      setRemovingId(null);
    }
  };

  const registeredTags = new Set(registered.map((model) => model.tag));
  const filteredDiscovered =
    discovered?.filter((model) =>
      model.id.toLowerCase().includes(modelQuery.trim().toLowerCase()),
    ) ?? null;

  return (
    <div className="space-y-4 pt-4 border-t">
      <div className="space-y-0.5">
        <p className="text-sm font-medium flex items-center gap-1.5">
          <Server className="w-3.5 h-3.5 text-muted-foreground" />
          {m.provider_routing_nine_router_title()}
        </p>
        <p className="text-xs text-muted-foreground max-w-[360px]">
          {m.provider_routing_nine_router_subtitle()}
        </p>
      </div>

      {error && (
        <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">
          {error}
        </p>
      )}

      {/* Endpoint + key */}
      {loadingStatus ? (
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      ) : status?.configured ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2">
          <div className="flex items-center gap-1.5 text-xs min-w-0">
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 shrink-0">
              {m.provider_routing_nine_router_configured()}
            </span>
            <span className="font-mono text-muted-foreground truncate">
              {status.base_url} · {status.masked}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive shrink-0"
            onClick={() => void handleRemoveConfig()}
            disabled={removingConfig}
          >
            {removingConfig ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              m.provider_routing_nine_router_config_remove()
            )}
          </Button>
        </div>
      ) : (
        <div className="space-y-1.5">
          <Input
            value={baseUrlInput}
            onChange={(e) => setBaseUrlInput(e.target.value)}
            placeholder={m.provider_routing_nine_router_base_url_placeholder()}
            className="h-8 text-xs font-mono"
            autoComplete="off"
          />
          <div className="flex gap-1.5">
            <Input
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder={m.provider_routing_nine_router_key_placeholder()}
              className="h-8 text-xs font-mono flex-1"
              autoComplete="off"
            />
            <Button
              size="sm"
              className="h-8"
              onClick={() => void handleSaveConfig()}
              disabled={
                savingConfig || !baseUrlInput.trim() || !keyInput.trim()
              }
            >
              {savingConfig ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                m.provider_routing_nine_router_config_save()
              )}
            </Button>
          </div>
        </div>
      )}

      {/* Descoberta */}
      {status?.configured && (
        <div className="space-y-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleDiscover()}
            disabled={discovering}
          >
            {discovering ? (
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            )}
            {m.provider_routing_detect_models()}
          </Button>

          {reachable === false && (
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_nine_router_unreachable()}
            </p>
          )}

          {discovered && discovered.length > 0 && (
            <div className="space-y-2">
              {discovered.length > 5 && (
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
                  <Input
                    value={modelQuery}
                    onChange={(e) => setModelQuery(e.target.value)}
                    placeholder={m.provider_routing_nine_router_search_placeholder()}
                    className="h-8 text-xs pl-8"
                    autoComplete="off"
                  />
                </div>
              )}

              {filteredDiscovered && filteredDiscovered.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {m.provider_routing_nine_router_search_empty()}
                </p>
              ) : (
                <div className="space-y-1.5">
                  {(filteredDiscovered ?? discovered).map((model) => {
                    const already = registeredTags.has(model.id);
                    return (
                      <div
                        key={model.id}
                        className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2"
                      >
                        <span className="text-sm font-mono truncate">
                          {model.id}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs shrink-0"
                          onClick={() => void handleRegister(model.id)}
                          disabled={already || registeringId === model.id}
                        >
                          {registeringId === model.id ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : already ? (
                            m.provider_routing_already_registered()
                          ) : (
                            <>
                              <Plus className="w-3.5 h-3.5 mr-1" />
                              {m.provider_routing_register()}
                            </>
                          )}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <RegisteredModelsList
        registered={registered}
        loading={loadingRegistered}
        removingId={removingId}
        onRemove={(id) => void handleRemove(id)}
      />
    </div>
  );
}

/** Modelos de imagem/TTS para os providers de gateway.
 *
 * Config por gateway (Ollama/OpenRouter), mesma natureza dos modelos LLM
 * registrados acima — não uma preferência genérica de usuário.
 *
 * Só Ollama e OpenRouter aparecem aqui: Gemini/OpenAI resolvem a capacidade
 * sozinhos pelo catálogo (`PROVIDER_CAPABILITIES`) e não têm o que escolher.
 * Campo vazio devolve o controle pra env var correspondente, se houver —
 * não desliga a capacidade. */
function MediaModelsSection() {
  const [models, setModels] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch("/admin/media-models");
        if (!res.ok || !alive) return;
        const data = (await res.json()) as { models?: Record<string, string> };
        if (alive) setModels(data.models ?? {});
      } catch {
        if (alive) setModels({});
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function handleSave(key: string, value: string) {
    setError("");
    try {
      const res = await fetch("/admin/media-models", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as { models?: Record<string, string> };
      // Repinta com o valor EFETIVO devolvido pelo backend: se o campo foi
      // limpo e existe env var, o que passa a valer é a env — mostrar o campo
      // vazio faria parecer que a capacidade ficou desligada.
      setModels(data.models ?? {});
    } catch {
      setError(m.prefs_media_models_error());
    }
  }

  const campos: { key: string; label: string }[] = [
    { key: "ollama_image_model", label: m.prefs_media_models_ollama_image() },
    { key: "ollama_tts_model", label: m.prefs_media_models_ollama_tts() },
    {
      key: "openrouter_image_model",
      label: m.prefs_media_models_openrouter_image(),
    },
    {
      key: "openrouter_tts_model",
      label: m.prefs_media_models_openrouter_tts(),
    },
  ];

  return (
    <div className="space-y-3">
      <Label>{m.prefs_media_models_section()}</Label>
      <p className="text-xs text-muted-foreground">
        {m.prefs_media_models_hint()}
      </p>
      {campos.map(({ key, label }) => (
        <div key={key} className="flex items-center justify-between gap-3">
          <Label htmlFor={key} className="text-xs font-normal">
            {label}
          </Label>
          <Input
            id={key}
            className="w-[240px]"
            value={models[key] ?? ""}
            autoComplete="off"
            onChange={(e) =>
              setModels((prev) => ({ ...prev, [key]: e.target.value }))
            }
            onBlur={(e) => void handleSave(key, e.target.value)}
          />
        </div>
      ))}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function LlamaCppSection() {
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8080/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<LlamaCppModelInfo[]>([]);
  const [hfModels, setHfModels] = useState<LlamaCppModelInfo[]>([]);
  const [query, setQuery] = useState("llama.cpp");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [releases, setReleases] = useState<LlamaCppReleaseAsset[]>([]);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    void fetch("/provider-routing/llamacpp/status")
      .then((response) => response.json())
      .then((data: { base_url?: string; model?: string }) => {
        setBaseUrl(data.base_url ?? "http://127.0.0.1:8080/v1");
        setModel(data.model ?? "");
      })
      .catch(() => undefined);
  }, []);

  async function saveConfig() {
    setError("");
    try {
      const response = await fetch("/provider-routing/llamacpp/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setApiKey("");
    } catch {
      setError("Erro ao salvar a configuração do llama.cpp");
    }
  }

  async function discover() {
    setLoading(true);
    setError("");
    try {
      const result = await discoverLlamaCppModels();
      setReachable(result.reachable);
      setModels(result.models);
    } catch {
      setError("Erro ao consultar o llama.cpp");
    } finally {
      setLoading(false);
    }
  }

  async function testConnection() {
    setLoading(true);
    setError("");
    try {
      const result = await testLlamaCppConnection();
      setReachable(result.status === "ok" || result.status === "empty");
      if (result.models) setModels(result.models);
      if (result.status !== "ok") setError(`Status: ${result.status}`);
    } catch {
      setReachable(false);
      setError("Erro ao testar o llama.cpp");
    } finally {
      setLoading(false);
    }
  }

  async function search() {
    setLoading(true);
    setError("");
    try {
      setHfModels(await searchHuggingFaceModels(query));
    } catch {
      setError("Erro ao consultar a Hugging Face");
    } finally {
      setLoading(false);
    }
  }

  async function installRuntime() {
    setLoading(true);
    setError("");
    try {
      const assets = await fetchLlamaCppReleases();
      setReleases(assets);
      const preferred = assets.find((asset) => /win|windows/i.test(asset.name));
      if (!preferred) throw new Error("Nenhum runtime compatível encontrado");
      setInstalling(true);
      await installLlamaCppRuntime(preferred.url);
    } catch {
      setError("Erro ao instalar o runtime oficial do llama.cpp");
    } finally {
      setInstalling(false);
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="font-medium">{m.provider_routing_llamacpp_title()}</h3>
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_llamacpp_subtitle()}
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          aria-label={m.provider_routing_llamacpp_endpoint()}
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder={m.provider_routing_llamacpp_endpoint_placeholder()}
        />
        <Input
          aria-label={m.provider_routing_llamacpp_model()}
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder={m.provider_routing_llamacpp_model_placeholder()}
        />
        <Input
          aria-label={m.provider_routing_llamacpp_key()}
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={m.provider_routing_llamacpp_key_placeholder()}
        />
        <Button
          type="button"
          onClick={() => void saveConfig()}
          disabled={loading}
        >
          {m.provider_routing_llamacpp_save()}
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => void installRuntime()}
          disabled={loading || installing}
        >
          {m.provider_routing_llamacpp_install()}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => void discover()}
          disabled={loading}
        >
          <RefreshCw className="mr-2 size-4" />{" "}
          {m.provider_routing_llamacpp_detect()}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => void testConnection()}
          disabled={loading}
        >
          {m.provider_routing_llamacpp_test()}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => void search()}
          disabled={loading}
        >
          <Search className="mr-2 size-4" /> {m.provider_routing_llamacpp_hf()}
        </Button>
      </div>
      {reachable !== null && (
        <p className="text-xs text-muted-foreground">
          {reachable
            ? m.provider_routing_llamacpp_found({ n: models.length })
            : m.provider_routing_llamacpp_unreachable()}
        </p>
      )}
      {hfModels.length > 0 && (
        <div className="space-y-1 text-sm">
          {hfModels.map((item) => (
            <div key={item.id}>{item.id}</div>
          ))}
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {releases.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_llamacpp_release_count({ n: releases.length })}
        </p>
      )}
    </div>
  );
}

export function ProviderRoutingTab() {
  return (
    <div className="space-y-4">
      <OllamaSection />
      <HuggingFaceCatalogSection />
      <OpenRouterSection />
      <NineRouterSection />
      <LlamaCppSection />
      <MediaModelsSection />
    </div>
  );
}

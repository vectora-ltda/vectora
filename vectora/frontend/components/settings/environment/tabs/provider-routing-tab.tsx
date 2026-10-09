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
import { useCallback, useEffect, useRef, useState } from "react";

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
  publisher?: string;
  name?: string;
  format?: string;
  compatibility?: string;
  architecture?: string;
  quantization?: string;
  context_length?: string;
}

interface HuggingFaceFileInfo {
  rfilename: string;
  size?: number | null;
  sha256?: string | null;
  format?: string;
}

interface HuggingFaceMetadata {
  id: string;
  publisher?: string;
  revision?: string;
  license?: string | null;
  downloads?: number;
  architecture?: string;
  quantization?: string;
  context_length?: number | string | null;
  compatibility?: string;
  files?: HuggingFaceFileInfo[];
}

interface LlamaCppReleaseAsset {
  name: string;
  url: string;
  size: number;
  sha256?: string;
  recommended?: boolean;
}

interface LlamaCppRuntimeStatus {
  installed: boolean;
  path: string | null;
  files: string[];
  free_bytes?: number;
  active_runtime?: string | null;
  runtimes: Array<{
    id?: string;
    asset: string;
    sha256: string;
    installed_at?: string;
    source?: string;
    version?: string;
  }>;
}

interface DmrStatus {
  configured: boolean;
  base_url: string;
  model: string;
  cli_available: boolean;
  reachable: boolean;
  contract: string | null;
  models: string[];
  managed_models: string[];
  detail?: string | null;
  state: "docker_unavailable" | "plugin_unavailable" | "stopped" | "ready";
  platform: string;
  architecture: string;
  backend: string | null;
  capabilities: {
    cpus: number | null;
    memory_bytes: number | null;
    runtimes: string[];
    gpu_backends: string[];
    warnings: string[];
  };
}

interface DmrJob {
  id: string;
  operation: "prepare" | "start";
  reference: string;
  context_size?: number | null;
  status:
    "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  output?: string;
  error?: string | null;
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

async function installLlamaCppRuntime(
  assetUrl: string,
  sha256?: string,
): Promise<void> {
  const res = await fetch("/provider-routing/llamacpp/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_url: assetUrl, sha256 }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function fetchLlamaCppRuntimeStatus(): Promise<LlamaCppRuntimeStatus> {
  const res = await fetch("/provider-routing/llamacpp/runtime/status");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json() as Promise<LlamaCppRuntimeStatus>;
}

async function fetchDmrStatus(): Promise<DmrStatus> {
  const response = await fetch("/provider-routing/dmr/status");
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrStatus;
}

async function configureDmr(
  baseUrl: string,
  model: string,
): Promise<DmrStatus> {
  const response = await fetch("/provider-routing/dmr/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url: baseUrl, model }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrStatus;
}

async function prepareDmrModel(reference: string): Promise<DmrStatus> {
  const response = await fetch("/provider-routing/dmr/models/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrStatus;
}

async function createDmrModelJob(
  reference: string,
  operation: "prepare" | "start" = "start",
  contextSize?: number,
): Promise<DmrJob> {
  const response = await fetch("/provider-routing/dmr/models/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reference,
      operation,
      ...(contextSize === undefined ? {} : { context_size: contextSize }),
    }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrJob;
}

async function fetchDmrModelJob(jobId: string): Promise<DmrJob> {
  const response = await fetch(`/provider-routing/dmr/models/jobs/${jobId}`);
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrJob;
}

async function fetchDmrModelJobs(): Promise<DmrJob[]> {
  const response = await fetch("/provider-routing/dmr/models/jobs");
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  const payload = (await response.json()) as { jobs?: DmrJob[] };
  return payload.jobs ?? [];
}

async function retryDmrModelJob(jobId: string): Promise<DmrJob> {
  const response = await fetch(
    `/provider-routing/dmr/models/jobs/${jobId}/retry`,
    {
      method: "POST",
    },
  );
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrJob;
}

async function cancelDmrModelJob(jobId: string): Promise<DmrJob> {
  const response = await fetch(`/provider-routing/dmr/models/jobs/${jobId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as DmrJob;
}

async function stopDmrModel(reference: string): Promise<DmrStatus> {
  const response = await fetch("/provider-routing/dmr/models/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return fetchDmrStatus();
}

async function removeDmrModel(reference: string): Promise<DmrStatus> {
  const response = await fetch(
    `/provider-routing/dmr/models/${encodeURIComponent(reference)}?confirm=true`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return fetchDmrStatus();
}

function DmrSection() {
  const [status, setStatus] = useState<DmrStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:12434");
  const [model, setModel] = useState("");
  const [contextSize, setContextSize] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<DmrJob | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchDmrStatus();
      setStatus(next);
      setBaseUrl(next.base_url);
      setModel(next.model);
      const jobs = await fetchDmrModelJobs();
      const active = jobs.find(
        (candidate) =>
          candidate.status === "queued" ||
          candidate.status === "running" ||
          candidate.status === "interrupted",
      );
      setJob(active ?? null);
    } catch {
      setError("Não foi possível consultar o Docker Model Runner.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function save() {
    setBusy(true);
    setError("");
    try {
      setStatus(await configureDmr(baseUrl, model));
    } catch {
      setError("Não foi possível salvar o endpoint do Docker Model Runner.");
    } finally {
      setBusy(false);
    }
  }

  async function prepare() {
    if (!model.trim()) return;
    setBusy(true);
    setError("");
    try {
      const parsedContext = contextSize.trim()
        ? Number(contextSize)
        : undefined;
      if (parsedContext !== undefined && !Number.isInteger(parsedContext)) {
        throw new Error("tamanho de contexto inválido");
      }
      let current = await createDmrModelJob(
        model.trim(),
        "start",
        parsedContext,
      );
      setJob(current);
      while (current.status === "queued" || current.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        current = await fetchDmrModelJob(current.id);
        setJob(current);
      }
      if (current.status !== "completed") {
        throw new Error(current.error ?? "operação DMR falhou");
      }
      setStatus(await fetchDmrStatus());
    } catch {
      setError("Não foi possível preparar o modelo no Docker Model Runner.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelPrepare() {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    try {
      setJob(await cancelDmrModelJob(job.id));
    } catch {
      setError("Não foi possível cancelar a operação do Docker Model Runner.");
    }
  }

  async function retryPrepare() {
    if (!job || job.status !== "interrupted") return;
    setBusy(true);
    setError("");
    try {
      let current = await retryDmrModelJob(job.id);
      setJob(current);
      while (current.status === "queued" || current.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        current = await fetchDmrModelJob(current.id);
        setJob(current);
      }
      if (current.status !== "completed") {
        throw new Error(current.error ?? "operação DMR falhou");
      }
      setStatus(await fetchDmrStatus());
    } catch {
      setError("Não foi possível retomar a operação do Docker Model Runner.");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!model.trim()) return;
    setBusy(true);
    setError("");
    try {
      setStatus(await stopDmrModel(model.trim()));
    } catch {
      setError("Não foi possível parar o modelo no Docker Model Runner.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!model.trim() || !window.confirm("Remover este modelo do Docker?"))
      return;
    setBusy(true);
    setError("");
    try {
      setStatus(await removeDmrModel(model.trim()));
    } catch {
      setError("Não foi possível remover o modelo do Docker Model Runner.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="font-medium">{m.provider_routing_dmr_title()}</h3>
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_dmr_subtitle()}
        </p>
      </div>
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_1fr_auto]">
        <Input
          aria-label={m.provider_routing_dmr_endpoint()}
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder={m.provider_routing_dmr_endpoint_placeholder()}
        />
        <Input
          aria-label={m.provider_routing_dmr_model()}
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder={m.provider_routing_dmr_model_placeholder()}
        />
        <Input
          aria-label={m.provider_routing_hf_ctx_size()}
          type="number"
          min={1}
          max={1000000}
          value={contextSize}
          onChange={(event) => setContextSize(event.target.value)}
          placeholder={m.provider_routing_hf_ctx_size()}
        />
        <Button type="button" disabled={busy} onClick={() => void save()}>
          {m.provider_routing_dmr_save()}
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => void refresh()}
        >
          {m.provider_routing_dmr_test()}
        </Button>
        {job && (job.status === "queued" || job.status === "running") ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => void cancelPrepare()}
          >
            {m.provider_routing_dmr_cancel()}
          </Button>
        ) : job?.status === "interrupted" ? (
          <Button
            type="button"
            disabled={busy}
            onClick={() => void retryPrepare()}
          >
            {m.provider_routing_dmr_prepare()}
          </Button>
        ) : (
          <Button
            type="button"
            disabled={busy || !model.trim()}
            onClick={() => void prepare()}
          >
            {m.provider_routing_dmr_prepare()}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={busy || !model.trim() || status?.state !== "ready"}
          onClick={() => void stop()}
        >
          {m.provider_routing_dmr_stop()}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={busy || !model.trim()}
          onClick={() => void remove()}
        >
          {m.provider_routing_dmr_remove()}
        </Button>
      </div>
      {status && (
        <p className="text-xs text-muted-foreground">
          {status.state === "ready"
            ? m.provider_routing_dmr_ready({ contract: status.contract ?? "?" })
            : status.state === "stopped"
              ? m.provider_routing_dmr_stopped()
              : status.state === "plugin_unavailable"
                ? m.provider_routing_dmr_missing_plugin()
                : m.provider_routing_dmr_missing_cli()}
          {` · ${status.platform}/${status.architecture}`}
          {status.backend ? ` · ${status.backend}` : ""}
          {status.capabilities.gpu_backends.length
            ? ` · GPU: ${status.capabilities.gpu_backends.join(", ")}`
            : ""}
          {status.models.length ? ` · ${status.models.join(", ")}` : ""}
          {status.managed_models.length
            ? ` · ${status.managed_models.join(", ")}`
            : ""}
        </p>
      )}
      {job && (job.status === "queued" || job.status === "running") && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_dmr_job_running({ status: job.status })}
        </p>
      )}
      {job?.status === "cancelled" && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_dmr_job_cancelled()}
        </p>
      )}
      {job?.status === "interrupted" && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_dmr_job_interrupted()}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

async function removeLlamaCppRuntime(): Promise<void> {
  const res = await fetch("/provider-routing/llamacpp/runtime", {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function rollbackLlamaCppRuntime(runtimeId: string): Promise<void> {
  const res = await fetch("/provider-routing/llamacpp/runtime/rollback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ runtime_id: runtimeId }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function removeLlamaCppRuntimeVersion(runtimeId: string): Promise<void> {
  const res = await fetch(`/provider-routing/llamacpp/runtime/${runtimeId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function cleanupLlamaCppRuntime(keep: number): Promise<void> {
  const res = await fetch("/provider-routing/llamacpp/runtime/cleanup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status}`);
}

async function searchHuggingFaceModels(
  query: string,
  provider: "ollama" | "llamacpp" | "dmr" = "llamacpp",
): Promise<LlamaCppModelInfo[]> {
  const res = await fetch(
    `/provider-routing/huggingface/models?q=${encodeURIComponent(query)}&provider=${provider}`,
  );
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { models?: LlamaCppModelInfo[] };
  return data.models ?? [];
}

async function downloadHuggingFaceModel(
  repoId: string,
  filename?: string,
  revision = "main",
  signal?: AbortSignal,
): Promise<{ path: string; filename: string }> {
  const metadataResponse = await fetch(
    `/provider-routing/huggingface/models/${repoId
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/")}`,
    { signal },
  );
  if (!metadataResponse.ok) throw new Error(`Erro ${metadataResponse.status}`);
  const metadata = (await metadataResponse.json()) as {
    files?: Array<{ rfilename: string; format?: string; sha256?: string }>;
  };
  const file = filename
    ? metadata.files?.find((item) => item.rfilename === filename)
    : metadata.files?.find((item) => item.format === "GGUF");
  if (!file) throw new Error("Nenhum arquivo GGUF encontrado");
  const response = await fetch("/provider-routing/huggingface/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_id: repoId,
      filename: file.rfilename,
      revision,
      ...(file.sha256 ? { sha256: file.sha256 } : {}),
    }),
    signal,
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  const result = (await response.json()) as { path: string };
  return { path: result.path, filename: file.rfilename };
}

async function fetchHuggingFaceMetadata(
  repoId: string,
): Promise<HuggingFaceMetadata> {
  const encodedRepo = repoId
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  const response = await fetch(
    `/provider-routing/huggingface/models/${encodedRepo}`,
  );
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as HuggingFaceMetadata;
}

async function installHuggingFaceModel(input: {
  repoId: string;
  revision: string;
  filename: string;
  mmprojFilename?: string;
  alias: string;
  provider: "ollama" | "llamacpp";
  license?: string | null;
  architecture?: string;
  quantization?: string;
  contextLength?: number | string | null;
  compatibility?: string;
}): Promise<void> {
  const response = await fetch("/provider-routing/huggingface/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_id: input.repoId,
      revision: input.revision,
      filename: input.filename,
      mmproj_filename: input.mmprojFilename,
      alias: input.alias,
      provider: input.provider,
      license: input.license,
      architecture: input.architecture,
      quantization: input.quantization,
      context_length: input.contextLength,
      compatibility: input.compatibility,
    }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  await registerModel(
    input.provider === "ollama" ? "ollama" : "llamacpp",
    input.alias,
  );
}

async function cancelHuggingFaceDownload(
  repoId: string,
  filename: string,
  revision: string,
): Promise<void> {
  const params = new URLSearchParams({ repo_id: repoId, filename, revision });
  await fetch(`/provider-routing/huggingface/download?${params.toString()}`, {
    method: "DELETE",
  });
}

async function fetchHuggingFaceDownloadProgress(
  repoId: string,
  filename: string,
  revision: string,
): Promise<{ downloaded: number; total: number | null; status: string }> {
  const params = new URLSearchParams({ repo_id: repoId, filename, revision });
  const response = await fetch(
    `/provider-routing/huggingface/download/progress?${params.toString()}`,
  );
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return (await response.json()) as {
    downloaded: number;
    total: number | null;
    status: string;
  };
}

async function startInstalledHuggingFaceModel(
  repoId: string,
  filename: string,
  options: {
    ctx_size?: number;
    n_gpu_layers?: number;
    threads?: number;
    parallel?: number;
    jinja: boolean;
  },
): Promise<void> {
  const response = await fetch("/provider-routing/huggingface/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_id: repoId, filename, ...options }),
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  const modeResponse = await fetch("/provider-routing/llamacpp/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "managed" }),
  });
  if (!modeResponse.ok) throw new Error(`Erro ${modeResponse.status}`);
}

function HuggingFaceCatalogSection() {
  const [provider, setProvider] = useState<"ollama" | "llamacpp" | "dmr">(
    "llamacpp",
  );
  const [query, setQuery] = useState("llama");
  const [models, setModels] = useState<LlamaCppModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloaded, setDownloaded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const downloadController = useRef<AbortController | null>(null);
  const [metadata, setMetadata] = useState<HuggingFaceMetadata | null>(null);
  const [selectedFile, setSelectedFile] = useState("");
  const [selectedMmproj, setSelectedMmproj] = useState("");
  const [alias, setAlias] = useState("");
  const [installing, setInstalling] = useState(false);
  const [downloadPercent, setDownloadPercent] = useState<number | null>(null);
  const [dmrJobId, setDmrJobId] = useState<string | null>(null);
  const [installedModel, setInstalledModel] = useState<{
    repoId: string;
    filename: string;
    options: {
      ctx_size?: number;
      n_gpu_layers?: number;
      threads?: number;
      parallel?: number;
      jinja: boolean;
    };
  } | null>(null);
  const [ctxSize, setCtxSize] = useState("4096");
  const [gpuLayers, setGpuLayers] = useState("");
  const [threads, setThreads] = useState("");
  const [parallel, setParallel] = useState("1");
  const [jinja, setJinja] = useState(false);

  function selectedFileSize(): string {
    const size = metadata?.files?.find(
      (item) => item.rfilename === selectedFile,
    )?.size;
    if (!size) return "?";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = size;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function selectedFileHash(): string {
    return (
      metadata?.files?.find((item) => item.rfilename === selectedFile)
        ?.sha256 || m.provider_routing_hf_metadata_unknown()
    );
  }

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

  async function prepareModel(repoId: string) {
    setError(null);
    try {
      const data = await fetchHuggingFaceMetadata(repoId);
      if (provider === "dmr") {
        setMetadata(data);
        setSelectedFile("");
        setSelectedMmproj("");
        setAlias(`hf.co/${repoId}`);
        return;
      }
      const gguf = (data.files ?? []).filter((item) => item.format === "GGUF");
      if (!gguf.length) throw new Error("Nenhum arquivo GGUF encontrado");
      setMetadata(data);
      setSelectedFile(gguf[0]?.rfilename ?? "");
      setSelectedMmproj(
        (data.files ?? []).find((item) => /mmproj/i.test(item.rfilename))
          ?.rfilename ?? "",
      );
      setAlias(gguf[0]?.rfilename ?? "");
    } catch {
      setError(m.provider_routing_hf_download_error());
    }
  }

  async function installSelectedModel() {
    if (!metadata || !alias.trim() || (provider !== "dmr" && !selectedFile)) {
      return;
    }
    const controller = new AbortController();
    downloadController.current = controller;
    setInstalling(true);
    setDownloadPercent(0);
    setError(null);
    let progressTimer: number | undefined;
    try {
      if (provider === "dmr") {
        let job = await createDmrModelJob(`hf.co/${metadata.id}`, "start");
        setDmrJobId(job.id);
        while (job.status === "queued" || job.status === "running") {
          await new Promise((resolve) => window.setTimeout(resolve, 500));
          job = await fetchDmrModelJob(job.id);
        }
        if (job.status !== "completed") {
          throw new Error(job.error ?? "operação DMR falhou");
        }
        setDownloaded(`hf.co/${metadata.id}`);
        setMetadata(null);
        return;
      }
      const revision = metadata.revision ?? "main";
      progressTimer = window.setInterval(() => {
        void fetchHuggingFaceDownloadProgress(
          metadata.id,
          selectedFile,
          revision,
        ).then((progress) => {
          if (progress.total && progress.total > 0) {
            setDownloadPercent(
              Math.min(
                100,
                Math.round((progress.downloaded / progress.total) * 100),
              ),
            );
          }
        });
      }, 500);
      await downloadHuggingFaceModel(
        metadata.id,
        selectedFile,
        revision,
        controller.signal,
      );
      if (selectedMmproj) {
        await downloadHuggingFaceModel(
          metadata.id,
          selectedMmproj,
          revision,
          controller.signal,
        );
      }
      await installHuggingFaceModel({
        repoId: metadata.id,
        revision,
        filename: selectedFile,
        mmprojFilename: selectedMmproj || undefined,
        alias: alias.trim(),
        provider,
        license: metadata.license,
        architecture: metadata.architecture,
        quantization: metadata.quantization,
        contextLength: metadata.context_length,
        compatibility: metadata.compatibility,
      });
      setDownloaded(alias.trim());
      setDownloadPercent(100);
      if (provider === "llamacpp") {
        setInstalledModel({
          repoId: metadata.id,
          filename: selectedFile,
          options: {
            ctx_size: Number(ctxSize) || undefined,
            n_gpu_layers: gpuLayers === "" ? undefined : Number(gpuLayers),
            threads: threads === "" ? undefined : Number(threads),
            parallel: Number(parallel) || undefined,
            jinja,
          },
        });
      }
      setMetadata(null);
    } catch (installError: unknown) {
      if (
        !(installError instanceof DOMException) ||
        installError.name !== "AbortError"
      ) {
        setError(m.provider_routing_hf_download_error());
      }
    } finally {
      if (downloadController.current === controller) {
        downloadController.current = null;
      }
      setDmrJobId(null);
      if (progressTimer !== undefined) window.clearInterval(progressTimer);
      setInstalling(false);
      setDownloadPercent(null);
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
          <option value="dmr">{m.provider_routing_dmr_title()}</option>
        </select>
        <Button type="button" onClick={() => void search()} disabled={loading}>
          <Search className="mr-2 size-4" /> {m.provider_routing_hf_search()}
        </Button>
      </div>
      {models.length > 0 && (
        <div className="space-y-2 text-sm">
          {models.map((model) => (
            <div
              key={model.id}
              className="flex items-center justify-between gap-3 rounded-md border bg-muted/10 px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{model.id}</p>
                <p className="text-xs text-muted-foreground">
                  {m.provider_routing_hf_origin({
                    publisher: model.publisher || model.id.split("/")[0] || "?",
                  })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {m.provider_routing_hf_model_metadata({
                    format: model.format || "GGUF",
                    compatibility:
                      model.compatibility ||
                      m.provider_routing_hf_compatibility_unknown(),
                    architecture:
                      model.architecture ||
                      m.provider_routing_hf_metadata_unknown(),
                    quantization:
                      model.quantization ||
                      m.provider_routing_hf_metadata_unknown(),
                    context:
                      model.context_length ||
                      m.provider_routing_hf_metadata_unknown(),
                  })}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={installing}
                onClick={() => void prepareModel(model.id)}
              >
                {m.provider_routing_hf_install()}
              </Button>
            </div>
          ))}
        </div>
      )}
      {metadata && (
        <div className="space-y-3 rounded-lg border bg-muted/20 p-4 text-sm">
          <div>
            <p className="font-medium">{metadata.id}</p>
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_hf_origin({
                publisher:
                  metadata.publisher || metadata.id.split("/")[0] || "?",
              })}
            </p>
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_hf_review({
                license: metadata.license ?? "desconhecida",
                downloads: String(metadata.downloads ?? 0),
              })}
            </p>
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_hf_file_details({
                revision: metadata.revision ?? "main",
                size: selectedFileSize(),
                sha256: selectedFileHash(),
              })}
            </p>
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_hf_model_metadata({
                format: "GGUF",
                compatibility:
                  metadata.compatibility ||
                  m.provider_routing_hf_compatibility_unknown(),
                architecture:
                  metadata.architecture ||
                  m.provider_routing_hf_metadata_unknown(),
                quantization:
                  metadata.quantization ||
                  m.provider_routing_hf_metadata_unknown(),
                context:
                  metadata.context_length ||
                  m.provider_routing_hf_metadata_unknown(),
              })}
            </p>
          </div>
          {provider === "dmr" ? (
            <p className="text-xs text-muted-foreground">
              {m.provider_routing_dmr_model()}: {`hf.co/${metadata.id}`}
            </p>
          ) : (
            <>
              <Label htmlFor="hf-model-file">
                {m.provider_routing_hf_model_file()}
              </Label>
              <select
                id="hf-model-file"
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={selectedFile}
                onChange={(event) => setSelectedFile(event.target.value)}
                disabled={installing}
              >
                {(metadata.files ?? [])
                  .filter((item) => item.format === "GGUF")
                  .map((item) => (
                    <option key={item.rfilename} value={item.rfilename}>
                      {item.rfilename}
                    </option>
                  ))}
              </select>
              <Label htmlFor="hf-mmproj-file">
                {m.provider_routing_hf_mmproj_file()}
              </Label>
              <select
                id="hf-mmproj-file"
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={selectedMmproj}
                onChange={(event) => setSelectedMmproj(event.target.value)}
                disabled={installing}
              >
                <option value="">{m.provider_routing_hf_mmproj_none()}</option>
                {(metadata.files ?? [])
                  .filter((item) => /mmproj/i.test(item.rfilename))
                  .map((item) => (
                    <option key={item.rfilename} value={item.rfilename}>
                      {item.rfilename}
                    </option>
                  ))}
              </select>
            </>
          )}
          <Input
            aria-label={m.provider_routing_hf_alias()}
            value={alias}
            onChange={(event) => setAlias(event.target.value)}
            placeholder={m.provider_routing_hf_alias()}
            disabled={installing}
          />
          {provider !== "dmr" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <Input
                aria-label={m.provider_routing_hf_ctx_size()}
                type="number"
                min={1}
                value={ctxSize}
                onChange={(event) => setCtxSize(event.target.value)}
                placeholder={m.provider_routing_hf_ctx_size()}
                disabled={installing}
              />
              <Input
                aria-label={m.provider_routing_hf_gpu_layers()}
                type="number"
                value={gpuLayers}
                onChange={(event) => setGpuLayers(event.target.value)}
                placeholder={m.provider_routing_hf_gpu_layers()}
                disabled={installing}
              />
              <Input
                aria-label={m.provider_routing_hf_threads()}
                type="number"
                min={1}
                value={threads}
                onChange={(event) => setThreads(event.target.value)}
                placeholder={m.provider_routing_hf_threads()}
                disabled={installing}
              />
              <Input
                aria-label={m.provider_routing_hf_parallel()}
                type="number"
                min={1}
                value={parallel}
                onChange={(event) => setParallel(event.target.value)}
                placeholder={m.provider_routing_hf_parallel()}
                disabled={installing}
              />
            </div>
          )}
          {provider !== "dmr" && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={jinja}
                onChange={(event) => setJinja(event.target.checked)}
                disabled={installing}
              />
              {m.provider_routing_hf_jinja()}
            </label>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={installing}
              onClick={() => setMetadata(null)}
            >
              {m.provider_routing_hf_cancel()}
            </Button>
            {installing ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (provider === "dmr" && dmrJobId) {
                    void cancelDmrModelJob(dmrJobId);
                    return;
                  }
                  downloadController.current?.abort();
                  if (metadata) {
                    const revision = metadata.revision ?? "main";
                    void Promise.all([
                      cancelHuggingFaceDownload(
                        metadata.id,
                        selectedFile,
                        revision,
                      ),
                      selectedMmproj
                        ? cancelHuggingFaceDownload(
                            metadata.id,
                            selectedMmproj,
                            revision,
                          )
                        : Promise.resolve(),
                    ]);
                  }
                }}
              >
                {m.provider_routing_hf_cancel()}
              </Button>
            ) : (
              <Button
                type="button"
                onClick={() => void installSelectedModel()}
                disabled={!selectedFile || !alias.trim()}
              >
                {m.provider_routing_hf_install()}
              </Button>
            )}
          </div>
        </div>
      )}
      {installing && downloadPercent !== null && (
        <p className="text-xs text-muted-foreground">
          {m.provider_routing_hf_download_progress({
            percent: String(downloadPercent),
          })}
        </p>
      )}
      {downloaded && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            {m.provider_routing_hf_downloaded({ path: downloaded })}
          </p>
          {installedModel && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setError(null);
                void startInstalledHuggingFaceModel(
                  installedModel.repoId,
                  installedModel.filename,
                  installedModel.options,
                ).catch(() =>
                  setError(m.provider_routing_llamacpp_unreachable()),
                );
              }}
            >
              {m.provider_routing_hf_start()}
            </Button>
          )}
        </div>
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
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:18080/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<"managed" | "external">("external");
  const [models, setModels] = useState<LlamaCppModelInfo[]>([]);
  const [hfModels, setHfModels] = useState<LlamaCppModelInfo[]>([]);
  const [query, setQuery] = useState("llama.cpp");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [releases, setReleases] = useState<LlamaCppReleaseAsset[]>([]);
  const [runtime, setRuntime] = useState<LlamaCppRuntimeStatus | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeKeep, setRuntimeKeep] = useState(1);

  useEffect(() => {
    void fetchLlamaCppRuntimeStatus()
      .then(setRuntime)
      .catch(() => setRuntime(null));
  }, []);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    void fetch("/provider-routing/llamacpp/status")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(
        (data: {
          base_url?: string;
          model?: string;
          mode?: "managed" | "external";
        }) => {
          setBaseUrl(data.base_url ?? "http://127.0.0.1:18080/v1");
          setModel(data.model ?? "");
          setMode(data.mode ?? "external");
        },
      )
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

  async function changeMode(nextMode: "managed" | "external") {
    setError("");
    try {
      const response = await fetch("/provider-routing/llamacpp/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: nextMode }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          detail?: string;
        };
        throw new Error(body.detail ?? String(response.status));
      }
      setMode(nextMode);
    } catch (modeError) {
      setError(
        modeError instanceof Error
          ? modeError.message
          : "Não foi possível alterar o modo do llama.cpp",
      );
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
      const preferred = assets.find((asset) => asset.recommended);
      if (!preferred) throw new Error("Nenhum runtime compatível encontrado");
      setInstalling(true);
      await installLlamaCppRuntime(preferred.url, preferred.sha256);
      setRuntime(await fetchLlamaCppRuntimeStatus());
    } catch {
      setError("Erro ao instalar o runtime oficial do llama.cpp");
    } finally {
      setInstalling(false);
      setLoading(false);
    }
  }

  async function removeRuntime() {
    if (!window.confirm("Remover o runtime gerenciado do llama.cpp?")) return;
    setRuntimeBusy(true);
    setError("");
    try {
      await removeLlamaCppRuntime();
      setRuntime(await fetchLlamaCppRuntimeStatus());
    } catch {
      setError("Não foi possível remover o runtime. Pare o sidecar antes.");
    } finally {
      setRuntimeBusy(false);
    }
  }

  async function cleanupRuntime() {
    if (!runtime) return;
    setRuntimeBusy(true);
    setError("");
    try {
      await cleanupLlamaCppRuntime(runtimeKeep);
      setRuntime(await fetchLlamaCppRuntimeStatus());
    } catch {
      setError(
        "Não foi possível limpar versões antigas. Pare o sidecar antes.",
      );
    } finally {
      setRuntimeBusy(false);
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
        <select
          aria-label={m.provider_routing_llamacpp_mode()}
          className="rounded-md border bg-background px-3 text-sm"
          value={mode}
          onChange={(event) =>
            void changeMode(event.target.value as "managed" | "external")
          }
          disabled={loading || installing}
        >
          <option value="external">
            {m.provider_routing_llamacpp_mode_external()}
          </option>
          <option value="managed">
            {m.provider_routing_llamacpp_mode_managed()}
          </option>
        </select>
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
      {runtime && (
        <div className="rounded-md border p-3 text-xs text-muted-foreground">
          <p>
            {m.provider_routing_llamacpp_title()}:{" "}
            {runtime.installed
              ? m.provider_routing_llamacpp_runtime_installed()
              : m.provider_routing_llamacpp_runtime_remove()}
            {runtime.runtimes.length > 0 &&
              ` (${runtime.runtimes.map((item) => item.asset).join(", ")})`}
          </p>
          {runtime.free_bytes !== undefined && (
            <p>
              {m.provider_routing_llamacpp_runtime_disk_free({
                bytes: String(runtime.free_bytes),
              })}
            </p>
          )}
          <div className="mt-2 space-y-1">
            {runtime.runtimes.map((item) => (
              <p key={item.id ?? item.asset} className="truncate">
                {m.provider_routing_llamacpp_runtime_details({
                  version: item.version || item.asset,
                  source: item.source || "—",
                  sha256: item.sha256 || "—",
                })}
              </p>
            ))}
          </div>
          {runtime.runtimes
            .filter((item) => item.id && item.id !== runtime.active_runtime)
            .map((item) => (
              <div key={item.id} className="mt-2 mr-2 inline-flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  className="px-0"
                  disabled={runtimeBusy || loading || installing}
                  onClick={() => {
                    if (!item.id) return;
                    setRuntimeBusy(true);
                    void rollbackLlamaCppRuntime(item.id)
                      .then(async () =>
                        setRuntime(await fetchLlamaCppRuntimeStatus()),
                      )
                      .catch(() =>
                        setError("Não foi possível reverter o runtime."),
                      )
                      .finally(() => setRuntimeBusy(false));
                  }}
                >
                  {m.provider_routing_llamacpp_runtime_rollback()} ({item.asset}
                  )
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="px-0"
                  disabled={runtimeBusy || loading || installing}
                  onClick={() => {
                    if (
                      !item.id ||
                      !window.confirm(
                        m.provider_routing_llamacpp_runtime_remove_version(),
                      )
                    )
                      return;
                    setRuntimeBusy(true);
                    void removeLlamaCppRuntimeVersion(item.id)
                      .then(async () =>
                        setRuntime(await fetchLlamaCppRuntimeStatus()),
                      )
                      .catch(() =>
                        setError(
                          "Não foi possível remover a versão do runtime.",
                        ),
                      )
                      .finally(() => setRuntimeBusy(false));
                  }}
                >
                  {m.provider_routing_llamacpp_runtime_remove_version()}
                </Button>
              </div>
            ))}
          {runtime.installed && (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <div className="grid gap-1">
                <Label htmlFor="llamacpp-runtime-keep" className="text-xs">
                  {m.provider_routing_llamacpp_runtime_keep_label()}
                </Label>
                <Input
                  id="llamacpp-runtime-keep"
                  type="number"
                  min={0}
                  max={50}
                  className="w-24"
                  value={runtimeKeep}
                  onChange={(event) =>
                    setRuntimeKeep(
                      Math.max(0, Math.min(50, Number(event.target.value))),
                    )
                  }
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={runtimeBusy || loading || installing}
                onClick={() => void cleanupRuntime()}
              >
                {m.provider_routing_llamacpp_runtime_cleanup()}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="px-0"
                disabled={runtimeBusy || loading || installing}
                onClick={() => void removeRuntime()}
              >
                {m.provider_routing_llamacpp_runtime_remove_all()}
              </Button>
            </div>
          )}
        </div>
      )}
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
      <DmrSection />
      <HuggingFaceCatalogSection />
      <OpenRouterSection />
      <NineRouterSection />
      <LlamaCppSection />
      <MediaModelsSection />
    </div>
  );
}

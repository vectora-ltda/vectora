"use client";

/**
 * MemorySection — Memory Library: GET /rag-library/catalog,
 * POST /rag-library/install, POST /rag-library/publish. Buckets RAG
 * pré-vetorizados publicados pela comunidade — download sempre grátis, sem
 * gate de tier.
 *
 * "Publicar" só fica habilitado com uma conta vectora.company conectada
 * (`useLicenseStatus().status?.configured`, mesmo VECTORA_TOKEN que o
 * license check usa) — sem conta conectada, mostra a nota explicativa em
 * vez de um botão que falharia sempre.
 */

import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Database,
  Download,
  Eye,
  Loader2,
  Pencil,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { m } from "@/lib/paraglide/messages";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useLibraryStore, type MemoryBucket } from "@/lib/stores/library-store";
import { LibraryCard, LibraryTag } from "./library-card";

interface RagBucketOption {
  id: string;
  name: string;
}

async function fetchWorkspaceBuckets(
  workspaceId: string,
): Promise<RagBucketOption[]> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/rag/buckets`,
  );
  if (!res.ok) return [];
  const data = (await res.json()) as { id: string; name: string }[];
  return data.map((b) => ({ id: b.id, name: b.name }));
}

async function installBucket(
  bucketId: string,
): Promise<{ status: string; error?: string }> {
  const res = await fetch("/rag-library/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bucket_id: bucketId }),
  });
  return res.json();
}

async function publishBucket(payload: {
  bucket_id: string;
  name: string;
  description: string;
  license: string;
}): Promise<{ status: string; bucket_id?: string; error?: string }> {
  const res = await fetch("/rag-library/publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

function PublishDialog({
  workspaceId,
  onClose,
  onPublished,
}: {
  workspaceId: string;
  onClose: () => void;
  onPublished: () => void;
}) {
  const [buckets, setBuckets] = useState<RagBucketOption[]>([]);
  const [bucketsLoading, setBucketsLoading] = useState(true);
  const [bucketId, setBucketId] = useState<string>("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [license, setLicense] = useState("MIT");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Sincroniza com o fetch dos buckets (sistema externo) ao trocar de
    // workspace — não deriva de estado puro.
    // oxlint-disable-next-line react/set-state-in-effect
    setBucketsLoading(true);
    void fetchWorkspaceBuckets(workspaceId).then((list) => {
      if (cancelled) return;
      setBuckets(list);
      setBucketsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  function handleSelectBucket(id: string) {
    setBucketId(id);
    const bucket = buckets.find((b) => b.id === id);
    if (bucket) setName(bucket.name);
  }

  const handleConfirm = async () => {
    if (!bucketId || !name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const result = await publishBucket({
        bucket_id: bucketId,
        name: name.trim(),
        description: description.trim(),
        license: license.trim() || "MIT",
      });
      if (result.status === "error") {
        setError(result.error ?? m.library_memory_error_publish());
        return;
      }
      onPublished();
      onClose();
    } catch {
      setError(m.library_memory_error_publish());
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.library_memory_publish_title()}</DialogTitle>
          <DialogDescription>
            {m.library_memory_publish_desc()}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {m.library_memory_publish_bucket()}
            </label>
            {bucketsLoading ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-1.5">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {m.library_memory_publish_bucket_loading()}
              </div>
            ) : buckets.length === 0 ? (
              <p className="text-xs text-muted-foreground py-1.5">
                {m.library_memory_publish_no_buckets()}
              </p>
            ) : (
              <Select value={bucketId} onValueChange={handleSelectBucket}>
                <SelectTrigger className="w-full">
                  <SelectValue
                    placeholder={m.library_memory_publish_bucket_placeholder()}
                  />
                </SelectTrigger>
                <SelectContent>
                  {buckets.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="publish-memory-name"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_memory_publish_name()}
            </label>
            <Input
              id="publish-memory-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="text-sm"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label
                htmlFor="publish-memory-description"
                className="text-xs font-medium text-muted-foreground"
              >
                {m.library_memory_publish_description()}
              </label>
              <button
                type="button"
                onClick={() => setPreviewing((v) => !v)}
                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                {previewing ? (
                  <>
                    <Pencil className="w-3 h-3" />
                    {m.library_memory_publish_edit()}
                  </>
                ) : (
                  <>
                    <Eye className="w-3 h-3" />
                    {m.library_memory_publish_preview()}
                  </>
                )}
              </button>
            </div>
            {previewing ? (
              <div className="rounded-md border px-3 py-2 text-sm min-h-24">
                <MarkdownView content={description || "*(sem descrição)*"} />
              </div>
            ) : (
              <Textarea
                id="publish-memory-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="text-sm font-mono"
                placeholder={m.library_memory_publish_description_placeholder()}
              />
            )}
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="publish-memory-license"
              className="text-xs font-medium text-muted-foreground"
            >
              {m.library_memory_publish_license()}
            </label>
            <Input
              id="publish-memory-license"
              value={license}
              onChange={(e) => setLicense(e.target.value)}
              className="text-sm font-mono"
              autoComplete="off"
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {m.envs_cancel()}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={saving || !bucketId || !name.trim()}
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
            {m.library_memory_publish_confirm()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BucketCard({
  bucket,
  currentEmbedModel,
}: {
  bucket: MemoryBucket;
  currentEmbedModel: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const incompatible =
    !!currentEmbedModel &&
    !!bucket.embed_model &&
    bucket.embed_model !== currentEmbedModel;

  const handleInstall = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await installBucket(bucket.id);
      if (result.status === "error") {
        setError(result.error ?? m.library_memory_error_install());
        return;
      }
      setInstalled(true);
    } catch {
      setError(m.library_memory_error_install());
    } finally {
      setBusy(false);
    }
  };

  return (
    <LibraryCard
      icon={<Database className="size-3.5" />}
      title={bucket.name}
      description={bucket.description}
      tags={
        <>
          <LibraryTag>
            {m.library_memory_embed_model({ model: bucket.embed_model })}
          </LibraryTag>
          {bucket.verified && (
            <LibraryTag verified>
              {m.library_memory_verified_badge()}
            </LibraryTag>
          )}
        </>
      }
      action={
        <Button
          variant={installed ? "outline" : "default"}
          size="sm"
          className={
            installed
              ? "h-[19px] rounded-md border-[#555] bg-transparent px-1.5 py-1 text-[9px] text-muted-foreground"
              : "h-[19px] rounded-md border-0 bg-[#d4d4d4] px-1.5 py-1 text-[9px] font-medium text-[#1a1a1a] hover:bg-white"
          }
          onClick={handleInstall}
          disabled={busy || installed}
        >
          {busy ? (
            <Loader2 className="size-[11px] animate-spin" />
          ) : installed ? (
            <>
              <CheckCircle2 className="size-[11px]" />
              {m.library_memory_installed()}
            </>
          ) : (
            <>
              <Download className="size-[11px]" />
              {m.library_memory_install()}
            </>
          )}
        </Button>
      }
      footer={
        <>
          {bucket.publisher && (
            <p className="text-[10px] leading-4 text-muted-foreground/80">
              {m.library_memory_publisher({ publisher: bucket.publisher })}
            </p>
          )}
          <p className="text-[10px] leading-4 text-muted-foreground/80">
            {m.library_memory_downloads({ count: bucket.downloads_count })}
          </p>
          {incompatible && !installed && (
            <p className="text-[10px] leading-4 text-amber-500">
              {m.library_memory_incompatible({ model: bucket.embed_model })}
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </>
      }
    />
  );
}

export function MemorySection({
  query,
  currentEmbedModel = null,
}: {
  query: string;
  currentEmbedModel?: string | null;
}) {
  const buckets = useLibraryStore((s) => s.memoryItems);
  const loading = useLibraryStore((s) => s.memoryLoading);
  const error = useLibraryStore((s) => s.memoryError);
  const ensureMemoryLoaded = useLibraryStore((s) => s.ensureMemoryLoaded);
  const invalidateMemory = useLibraryStore((s) => s.invalidateMemory);
  const workspaceId = useWorkspacesStore((s) => s.getActive()?.id);
  const { status: licenseStatus } = useLicenseStatus();
  const [publishing, setPublishing] = useState(false);
  const canPublish = Boolean(licenseStatus?.configured && workspaceId);

  useEffect(() => {
    if (!query.trim()) {
      void ensureMemoryLoaded(query);
      return;
    }
    const timer = setTimeout(() => {
      void ensureMemoryLoaded(query);
    }, 350);
    return () => clearTimeout(timer);
  }, [query, ensureMemoryLoaded]);

  if (loading) {
    return (
      <div className="flex justify-center py-6">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-2 py-1">
      {error && <p className="text-xs text-destructive">{error}</p>}
      {buckets.length === 0 ? (
        <p className="py-4 text-xs text-muted-foreground text-center">
          {m.library_empty_memory()}
        </p>
      ) : (
        buckets.map((bucket) => (
          <BucketCard
            key={bucket.id}
            bucket={bucket}
            currentEmbedModel={currentEmbedModel}
          />
        ))
      )}
      {canPublish ? (
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs w-full"
          onClick={() => setPublishing(true)}
        >
          <Upload className="w-3 h-3 mr-1.5" />
          {m.library_memory_publish_button()}
        </Button>
      ) : (
        <p className="text-[10px] text-muted-foreground/70 pt-1">
          {m.library_memory_publish_note()}
        </p>
      )}
      {publishing && workspaceId && (
        <PublishDialog
          workspaceId={workspaceId}
          onClose={() => setPublishing(false)}
          onPublished={() => {
            invalidateMemory();
            void ensureMemoryLoaded(query);
          }}
        />
      )}
    </div>
  );
}

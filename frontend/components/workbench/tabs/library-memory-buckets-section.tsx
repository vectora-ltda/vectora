"use client";

/**
 * MemoryBucketsSection — Memory Buckets: GET /memory-buckets/catalog,
 * POST /memory-buckets/install. Buckets RAG pré-vetorizados publicados pela
 * comunidade — download sempre grátis, sem gate de tier.
 */

import { useEffect, useState } from "react";
import { CheckCircle2, Database, Download, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { m } from "@/lib/paraglide/messages";
import { useLibraryStore, type MemoryBucket } from "@/lib/stores/library-store";
import { LibraryCard, LibraryTag } from "./library-card";

async function installBucket(
  bucketId: string,
): Promise<{ status: string; error?: string }> {
  const res = await fetch("/memory-buckets/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bucket_id: bucketId }),
  });
  const payload: unknown = await res.json();
  if (!res.ok || !payload || typeof payload !== "object") {
    throw new Error("memory bucket installation failed");
  }
  return payload as { status: string; error?: string };
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
      if (result.status !== "installed") {
        setError(result.error ?? m.library_memory_buckets_error_install());
        return;
      }
      setInstalled(true);
    } catch {
      setError(m.library_memory_buckets_error_install());
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
            {m.library_memory_buckets_embed_model({
              model: bucket.embed_model,
            })}
          </LibraryTag>
          {bucket.verified && (
            <LibraryTag verified>
              {m.library_memory_buckets_verified_badge()}
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
              {m.library_memory_buckets_installed()}
            </>
          ) : (
            <>
              <Download className="size-[11px]" />
              {m.library_memory_buckets_install()}
            </>
          )}
        </Button>
      }
      footer={
        <>
          {bucket.publisher && (
            <p className="text-[10px] leading-4 text-muted-foreground/80">
              {m.library_memory_buckets_publisher({
                publisher: bucket.publisher,
              })}
            </p>
          )}
          <p className="text-[10px] leading-4 text-muted-foreground/80">
            {m.library_memory_buckets_downloads({
              count: bucket.downloads_count,
            })}
          </p>
          {incompatible && !installed && (
            <p className="text-[10px] leading-4 text-amber-500">
              {m.library_memory_buckets_incompatible({
                model: bucket.embed_model,
              })}
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </>
      }
    />
  );
}

export function MemoryBucketsSection({
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
    </div>
  );
}

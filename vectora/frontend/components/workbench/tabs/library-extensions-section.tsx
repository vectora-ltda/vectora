import { Check, Download, Package } from "lucide-react";
import { useEffect } from "react";

import { m } from "@/lib/paraglide/messages";
import {
  NATIVE_EXTENSION_IDS,
  useLibraryStore,
  type VextExtension,
} from "@/lib/stores/library-store";

export function ExtensionsSection({
  query,
  onCountChange,
}: {
  query: string;
  onCountChange: (count: number) => void;
}) {
  const items = useLibraryStore((s) => s.extensionItems);
  const loading = useLibraryStore((s) => s.extensionLoading);
  const error = useLibraryStore((s) => s.extensionError);
  const ensure = useLibraryStore((s) => s.ensureExtensionsLoaded);
  useEffect(() => {
    void ensure(query);
  }, [ensure, query]);
  const filtered = items.filter(
    (item) =>
      !item.native &&
      !NATIVE_EXTENSION_IDS.has(item.id) &&
      (item.name.toLowerCase().includes(query.toLowerCase()) ||
        item.description.toLowerCase().includes(query.toLowerCase())),
  );
  useEffect(
    () => onCountChange(filtered.length),
    [filtered.length, onCountChange],
  );
  if (loading)
    return (
      <p className="p-3 text-xs text-muted-foreground">
        {m.library_extensions_loading()}
      </p>
    );
  if (error) return <p className="p-3 text-xs text-destructive">{error}</p>;
  if (!filtered.length)
    return (
      <p className="p-3 text-xs text-muted-foreground">
        {m.library_extensions_empty()}
      </p>
    );
  return (
    <div className="space-y-2 p-2">
      {filtered.map((extension) => (
        <ExtensionCard
          key={`${extension.id}:${extension.version}`}
          extension={extension}
        />
      ))}
    </div>
  );
}

function ExtensionCard({ extension }: { extension: VextExtension }) {
  const installed = useLibraryStore((s) =>
    s.extensionInstalledIds.has(extension.id),
  );
  const installing = useLibraryStore(
    (s) => s.extensionInstallingId === extension.id,
  );
  const install = useLibraryStore((s) => s.installExtension);
  const download = () => void install(extension);
  return (
    <article className="rounded-md border border-border/60 bg-card/30 p-2 min-w-0">
      <div className="flex items-start gap-2">
        {extension.icon ? (
          <img
            src={extension.icon}
            alt=""
            className="mt-0.5 h-5 w-5 shrink-0 object-contain"
          />
        ) : (
          <Package className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        )}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-xs font-medium">{extension.name}</h3>
          <p className="truncate text-[11px] text-muted-foreground">
            {extension.publisher === "official"
              ? "Vectora"
              : extension.publisher}{" "}
            · {extension.version}
          </p>
        </div>
        <button
          type="button"
          onClick={download}
          aria-label={`${installed ? m.library_extensions_installed() : m.library_extensions_install()} ${extension.name}`}
          disabled={installed || installing}
          className="inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-70"
        >
          {installed ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          <span>
            {installed
              ? m.library_extensions_installed()
              : installing
                ? m.library_extensions_loading()
                : m.library_extensions_install()}
          </span>
        </button>
      </div>
      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
        {extension.description}
      </p>
    </article>
  );
}

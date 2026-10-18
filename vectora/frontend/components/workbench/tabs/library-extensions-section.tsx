import { BookOpen, Check, Download, Package, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { MarkdownPreviewDialog } from "@/components/workbench/markdown-preview-dialog";
import { m } from "@/lib/paraglide/messages";
import {
  NATIVE_EXTENSION_IDS,
  useLibraryStore,
  type VextExtension,
} from "@/lib/stores/library-store";

export function ExtensionsSection({ query, onCountChange }: { query: string; onCountChange: (count: number) => void }) {
  const items = useLibraryStore((s) => s.extensionItems);
  const loading = useLibraryStore((s) => s.extensionLoading);
  const error = useLibraryStore((s) => s.extensionError);
  const ensure = useLibraryStore((s) => s.ensureExtensionsLoaded);
  useEffect(() => { void ensure(query); }, [ensure, query]);
  const filtered = items.filter((item) => !item.native && !NATIVE_EXTENSION_IDS.has(item.id) && (item.name.toLowerCase().includes(query.toLowerCase()) || item.description.toLowerCase().includes(query.toLowerCase())));
  useEffect(() => onCountChange(filtered.length), [filtered.length, onCountChange]);
  if (loading) return <p className="p-3 text-xs text-muted-foreground">{m.library_extensions_loading()}</p>;
  if (error) return <p className="p-3 text-xs text-destructive">{error}</p>;
  if (!filtered.length) return <p className="p-3 text-xs text-muted-foreground">{m.library_extensions_empty()}</p>;
  return <div className="space-y-2 p-2">{filtered.map((extension) => <ExtensionCard key={`${extension.id}:${extension.version}`} extension={extension} />)}</div>;
}

function ExtensionCard({ extension }: { extension: VextExtension }) {
  const installed = useLibraryStore((s) => s.extensionInstalledIds.has(extension.id));
  const busy = useLibraryStore((s) => s.extensionInstallingId === extension.id);
  const install = useLibraryStore((s) => s.installExtension);
  const uninstall = useLibraryStore((s) => s.uninstallExtension);
  const [versions, setVersions] = useState<string[]>([extension.version]);
  const [selectedVersion, setSelectedVersion] = useState(extension.version);
  const [readmeOpen, setReadmeOpen] = useState(false);
  const [readme, setReadme] = useState<string | undefined>();
  const [readmeLoading, setReadmeLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetch(`/registry/extensions/${encodeURIComponent(extension.id)}/versions`)
      .then((response) => (response.ok ? response.json() : { entries: [] }))
      .then((data: { entries?: VextExtension[] }) => {
        if (!cancelled) setVersions([...new Set([extension.version, ...(data.entries ?? []).map((item) => item.version)])]);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [extension.id, extension.version]);

  const openReadme = () => {
    setReadmeLoading(true);
    void fetch(`/registry/extensions/${encodeURIComponent(extension.id)}/readme`)
      .then(async (response) => { if (!response.ok) throw new Error("registry readme unavailable"); return (await response.json()) as { content?: string }; })
      .then((data) => setReadme(data.content ?? ""))
      .catch(() => fetch(`/vext/${encodeURIComponent(extension.id)}/readme`).then((response) => response.ok ? response.text() : "").then(setReadme).catch(() => setReadme("")))
      .finally(() => { setReadmeLoading(false); setReadmeOpen(true); });
  };

  const selectedIsInstalled = installed && selectedVersion === extension.version;
  return (
    <article className="rounded-md border border-border/60 bg-card/30 p-2 min-w-0">
      <div className="flex items-start gap-2">
        {extension.icon ? <img src={extension.icon} alt="" className="mt-0.5 h-5 w-5 shrink-0 object-contain" /> : <Package className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}
        <div className="min-w-0 flex-1"><h3 className="truncate text-xs font-medium">{extension.name}</h3><p className="truncate text-[11px] text-muted-foreground">{extension.publisher === "official" ? "Vectora" : extension.publisher} · {extension.version}</p></div>
        {versions.length > 1 && <select aria-label={`${m.library_extensions_version()} ${extension.name}`} value={selectedVersion} onChange={(event) => setSelectedVersion(event.target.value)} className="max-w-20 rounded border border-border/60 bg-background px-1 py-1 text-[10px]">{versions.map((version) => <option key={version} value={version}>{version}</option>)}</select>}
        <button type="button" onClick={() => void install({ ...extension, version: selectedVersion })} aria-label={`${selectedIsInstalled ? m.library_extensions_installed() : m.library_extensions_install()} ${extension.name}`} disabled={busy || selectedIsInstalled} className="inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-70">
          {selectedIsInstalled ? <Check className="h-3.5 w-3.5" /> : <Download className="h-3.5 w-3.5" />}<span>{selectedIsInstalled ? m.library_extensions_installed() : busy ? m.library_extensions_loading() : m.library_extensions_install()}</span>
        </button>
      </div>
      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{extension.description}</p>
      <div className="mt-2 flex items-center gap-1">
        <button type="button" onClick={openReadme} className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"><BookOpen className="h-3.5 w-3.5" /> {m.library_extensions_readme()}</button>
        {installed && <button type="button" onClick={() => void uninstall(extension.id)} disabled={busy} className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] text-destructive hover:bg-destructive/10 disabled:opacity-70"><Trash2 className="h-3.5 w-3.5" /> {m.library_extensions_uninstall()}</button>}
      </div>
      <MarkdownPreviewDialog open={readmeOpen} onOpenChange={setReadmeOpen} filePath={readmeLoading ? m.library_extensions_readme_loading() : `${extension.name} README`} content={readme} assetBaseUrl={`/vext/${encodeURIComponent(extension.id)}/asset`} />
    </article>
  );
}

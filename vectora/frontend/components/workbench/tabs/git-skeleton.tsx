/**
 * GitSkeleton — placeholder de carregamento da aba Git do workbench.
 * Simula o cabeçalho de grupo (Staged/Modificados) e algumas entradas de
 * arquivo com badge de status, para que a forma do skeleton já anuncie o
 * layout que vai aparecer.
 */

import { memo } from "react";

function FileRowPlaceholder({
  width,
  opacity,
}: {
  width: string;
  opacity: number;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5" style={{ opacity }}>
      <div className="h-3 w-3 shrink-0 rounded-full bg-muted/50 animate-pulse" />
      <div
        className={`h-2.5 ${width} rounded-full bg-muted/40 animate-pulse`}
      />
      <div className="ml-auto h-2.5 w-8 rounded-full bg-muted/30 animate-pulse" />
    </div>
  );
}

export const GitSkeleton = memo(function GitSkeleton() {
  return (
    <div className="py-2 space-y-4" aria-hidden="true">
      <div>
        <div className="px-3 py-1">
          <div className="h-2.5 w-16 rounded-full bg-emerald-500/30 animate-pulse" />
        </div>
        <FileRowPlaceholder width="w-40" opacity={0.9} />
        <FileRowPlaceholder width="w-28" opacity={0.75} />
      </div>
      <div>
        <div className="px-3 py-1">
          <div className="h-2.5 w-32 rounded-full bg-amber-500/30 animate-pulse" />
        </div>
        <FileRowPlaceholder width="w-36" opacity={0.6} />
        <FileRowPlaceholder width="w-24" opacity={0.45} />
        <FileRowPlaceholder width="w-32" opacity={0.3} />
      </div>
    </div>
  );
});

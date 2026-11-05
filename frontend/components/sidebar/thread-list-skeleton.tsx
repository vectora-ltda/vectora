/**
 * ThreadListSkeleton — placeholder de carregamento da lista de sessões na
 * sidebar. Extraído do bloco inline que existia em `sidebar.tsx` para
 * reuso (ex.: dentro de grupos de workspace recém-expandidos).
 */

import { memo } from "react";

export const ThreadListSkeleton = memo(function ThreadListSkeleton({
  rows = 5,
}: {
  rows?: number;
}) {
  return (
    <div className="mt-4 px-3 space-y-2" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="px-3 py-2.5 rounded-lg"
          style={{ opacity: 1 - i * 0.12 }}
        >
          <div className="h-3 w-3/4 rounded-full bg-muted/60 animate-pulse mb-1.5" />
          <div className="h-2 w-1/3 rounded-full bg-muted/40 animate-pulse" />
        </div>
      ))}
    </div>
  );
});

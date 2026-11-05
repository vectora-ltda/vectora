/**
 * FileTreeSkeleton — placeholder de carregamento da árvore de arquivos do
 * workbench. Simula linhas alternando ícone de pasta/arquivo com
 * indentação decrescente, para sugerir hierarquia sem dados reais.
 */

import { memo } from "react";

const ROW_SHAPES: Array<{ indent: number; width: string; isDir: boolean }> = [
  { indent: 0, width: "w-24", isDir: true },
  { indent: 1, width: "w-32", isDir: false },
  { indent: 1, width: "w-20", isDir: false },
  { indent: 1, width: "w-28", isDir: true },
  { indent: 2, width: "w-24", isDir: false },
  { indent: 2, width: "w-16", isDir: false },
  { indent: 0, width: "w-20", isDir: true },
  { indent: 1, width: "w-28", isDir: false },
];

export const FileTreeSkeleton = memo(function FileTreeSkeleton({
  rows = ROW_SHAPES.length,
}: {
  rows?: number;
}) {
  const shapes = ROW_SHAPES.slice(0, rows);
  return (
    <div className="py-1.5 space-y-1" aria-hidden="true">
      {shapes.map((shape, i) => (
        <div
          key={i}
          className="flex items-center gap-1.5 px-2 py-0.5"
          style={{ paddingLeft: 8 + shape.indent * 12, opacity: 1 - i * 0.07 }}
        >
          <div
            className={`h-3 w-3 shrink-0 rounded-sm bg-muted/50 animate-pulse ${shape.isDir ? "rounded-[3px]" : "rounded-full"}`}
          />
          <div
            className={`h-2.5 ${shape.width} rounded-full bg-muted/40 animate-pulse`}
          />
        </div>
      ))}
    </div>
  );
});

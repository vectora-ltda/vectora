"use client";

/**
 * CompareView — comparar dois refs (estilo VS Code) e base de merge/PR.
 *
 * Dois seletores (base / comparar) populados das branches; lista de arquivos
 * alterados (clicáveis → diff por arquivo, lazy); rodapé com "Merge na branch
 * atual" e "Criar PR". Quando o merge gera conflitos, a resolução
 * ours/theirs aparece inline — único lugar onde conflitos fazem sentido.
 */

import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  GitMerge,
  Loader2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { DiffHunk } from "@/lib/stores/workbench-store";
import {
  apiCompare,
  apiCompareFile,
  apiMerge,
  apiResolveConflict,
  type CompareFile,
  type CompareResult,
} from "./api";
import { HunkView, statusTone } from "./shared";
import { m } from "@/lib/paraglide/messages";

function CompareFileRow({
  workspaceId,
  baseRef,
  head,
  file,
}: {
  workspaceId: string;
  baseRef: string;
  head: string;
  file: CompareFile;
}) {
  const [open, setOpen] = useState(false);
  const [hunks, setHunks] = useState<DiffHunk[] | null>(null);

  const handleToggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (next && hunks === null) {
      const h = await apiCompareFile(workspaceId, baseRef, head, file.path);
      setHunks(h);
    }
  }, [open, hunks, workspaceId, baseRef, head, file.path]);

  return (
    <div className="border-b border-border/40 last:border-0">
      <button
        onClick={() => void handleToggle()}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs hover:bg-muted/30 text-left min-w-0"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground" />
        )}
        <span
          className={`w-4 text-center font-bold shrink-0 ${statusTone(file.status)}`}
        >
          {file.status}
        </span>
        <span className="flex-1 truncate font-mono">{file.path}</span>
        <span className="text-git-addition shrink-0">+{file.additions}</span>
        <span className="text-destructive shrink-0">−{file.deletions}</span>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-1">
          {hunks === null ? (
            <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
          ) : (
            hunks.map((h, i) => <HunkView key={i} hunk={h} />)
          )}
        </div>
      )}
    </div>
  );
}

export function CompareView({
  workspaceId,
  branches,
  current,
  onBack,
  onChanged,
  onOpenPR,
}: {
  workspaceId: string;
  branches: string[];
  current: string;
  onBack: () => void;
  onChanged: () => void;
  onOpenPR: (head: string) => void;
}) {
  const [baseRef, setBaseRef] = useState(current);
  const [head, setHead] = useState(
    branches.find((b) => b !== current) ?? current,
  );
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [merging, setMerging] = useState(false);
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [mergeMsg, setMergeMsg] = useState("");

  const runCompare = useCallback(async () => {
    if (!baseRef || !head || baseRef === head) {
      setResult(null);
      return;
    }
    setLoading(true);
    const r = await apiCompare(workspaceId, baseRef, head);
    setLoading(false);
    setResult(r);
  }, [workspaceId, baseRef, head]);

  useEffect(() => {
    // Recompara ao trocar base/head (rede), não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    void runCompare();
  }, [runCompare]);

  const handleMerge = async () => {
    setMerging(true);
    setMergeMsg("");
    setConflicts([]);
    try {
      const r = await apiMerge(workspaceId, head);
      if (r.status === "ok") {
        setMergeMsg(m.workbench_git_merge_ok());
        onChanged();
        void runCompare();
      } else if (r.status === "conflict") {
        setMergeMsg(m.workbench_git_merge_conflict());
        setConflicts(r.conflicts);
      } else {
        setMergeMsg(r.message);
      }
    } finally {
      setMerging(false);
    }
  };

  const resolve = useCallback(
    async (path: string, resolution: "ours" | "theirs") => {
      await apiResolveConflict(workspaceId, path, resolution);
      setConflicts((c) => c.filter((p) => p !== path));
    },
    [workspaceId],
  );

  return (
    <div className="h-full flex flex-col">
      {/* Pickers */}
      <div className="px-2 py-1.5 border-b border-border/60 bg-muted/10 flex items-center gap-1.5">
        <button
          onClick={onBack}
          title={m.workbench_git_back()}
          className="p-1 rounded hover:bg-muted/40 text-muted-foreground hover:text-foreground shrink-0"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
        </button>
        <select
          value={baseRef}
          onChange={(e) => setBaseRef(e.target.value)}
          className="flex-1 min-w-0 text-xs font-mono bg-background border border-border/60 rounded px-1.5 py-0.5 outline-none focus:border-primary"
          aria-label={m.workbench_git_compare_base()}
        >
          {branches.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-muted-foreground shrink-0">…</span>
        <select
          value={head}
          onChange={(e) => setHead(e.target.value)}
          className="flex-1 min-w-0 text-xs font-mono bg-background border border-border/60 rounded px-1.5 py-0.5 outline-none focus:border-primary"
          aria-label={m.workbench_git_compare_head()}
        >
          {branches.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
      </div>

      {/* Lista de arquivos */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
          </div>
        ) : !result || result.files.length === 0 ? (
          <p className="text-xs text-muted-foreground text-center py-8 px-4">
            {m.workbench_git_compare_no_files()}
          </p>
        ) : (
          <>
            <div className="px-2 py-1 text-[10px] text-muted-foreground border-b border-border/40">
              {m.workbench_git_compare_summary({
                ahead: result.ahead,
                behind: result.behind,
              })}
            </div>
            {result.files.map((f) => (
              <CompareFileRow
                key={f.path}
                workspaceId={workspaceId}
                baseRef={baseRef}
                head={head}
                file={f}
              />
            ))}
          </>
        )}
      </div>

      {/* Conflitos (inline, só após merge conflituoso) */}
      {conflicts.length > 0 && (
        <div className="border-t border-border/60 max-h-40 overflow-y-auto shrink-0">
          {conflicts.map((path) => (
            <div
              key={path}
              className="px-3 py-2 border-b border-border/40 last:border-0"
            >
              <p
                className="text-xs font-mono text-git-modification truncate mb-1.5"
                title={path}
              >
                {path}
              </p>
              <div className="flex gap-1.5">
                <button
                  onClick={() => void resolve(path, "ours")}
                  className="text-[10px] px-2 py-0.5 rounded bg-git-information/10 text-git-information hover:bg-git-information/20"
                >
                  {m.workbench_diff_conflicts_ours()}
                </button>
                <button
                  onClick={() => void resolve(path, "theirs")}
                  className="text-[10px] px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
                >
                  {m.workbench_diff_conflicts_theirs()}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Rodapé: merge + PR */}
      <div className="border-t border-border/60 p-2 flex flex-col gap-1.5 bg-muted/10 shrink-0">
        {mergeMsg && (
          <p className="text-[10px] text-muted-foreground">{mergeMsg}</p>
        )}
        <div className="flex gap-1.5">
          <button
            onClick={() => void handleMerge()}
            disabled={merging || head === current}
            className="flex flex-1 items-center justify-center gap-1.5 py-1 text-xs rounded-md border border-border/60 hover:bg-muted/40 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {merging ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <GitMerge className="w-3 h-3" />
            )}
            {m.workbench_git_merge_into({ branch: current })}
          </button>
          <button
            onClick={() => onOpenPR(head)}
            className="py-1 px-2 text-xs rounded-md bg-primary/10 text-primary hover:bg-primary/20"
          >
            {m.workbench_git_pr_create()}
          </button>
        </div>
      </div>
    </div>
  );
}

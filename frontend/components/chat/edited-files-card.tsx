"use client";

import { FilePlus2, GitBranch } from "lucide-react";
import { useState } from "react";
import type { EditedFile } from "@/lib/types";
import { m } from "@/lib/paraglide/messages";

interface EditedFilesCardProps {
  files: EditedFile[];
  onOpenFile: (file: EditedFile) => void;
}

export function EditedFilesCard({ files, onOpenFile }: EditedFilesCardProps) {
  const [expanded, setExpanded] = useState(false);
  if (files.length === 0) return null;
  const visible = expanded ? files : files.slice(0, 3);
  const hasMore = files.length > 3;
  const remaining = files.length - 3;
  const additions = files.reduce((sum, file) => sum + file.additions, 0);
  const deletions = files.reduce((sum, file) => sum + file.deletions, 0);

  return (
    <section
      className="mt-3 overflow-hidden rounded-xl border border-border/70 bg-card/70 text-sm"
      aria-label={m.chat_edited_files_review()}
      data-testid="edited-files-card"
    >
      <header className="flex items-center gap-3 border-b border-border/60 px-4 py-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background text-muted-foreground">
          <FilePlus2 className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold leading-5">
            {files.length === 1
              ? m.chat_edited_files_title_one()
              : m.chat_edited_files_title({ count: files.length })}
          </p>
          <p className="font-mono text-xs leading-4">
            <span className="text-git-addition">+{additions}</span>{" "}
            <span className="text-destructive">−{deletions}</span>
          </p>
        </div>
        <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
          {m.chat_edited_files_review()}
        </span>
      </header>
      <div>
        {visible.map((file) => (
          <button
            key={file.path}
            type="button"
            className="flex w-full min-w-0 items-center gap-3 border-b border-border/40 px-4 py-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
            onClick={() => onOpenFile(file)}
            title={m.chat_edited_files_diff({ path: file.path })}
          >
            <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-mono text-xs sm:text-sm">
              {file.path}
            </span>
            <span className="shrink-0 font-mono text-xs">
              <span className="text-git-addition">+{file.additions}</span>{" "}
              <span className="text-destructive">−{file.deletions}</span>
            </span>
          </button>
        ))}
      </div>
      {hasMore && (
        <button
          type="button"
          className="w-full px-4 py-2.5 text-left text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded
            ? m.chat_edited_files_show_less()
            : m.chat_edited_files_show_more({ count: remaining })}
        </button>
      )}
    </section>
  );
}

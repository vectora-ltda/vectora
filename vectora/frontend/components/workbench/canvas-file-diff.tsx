import type { EditedFile } from "@/lib/types";
import { m } from "@/lib/paraglide/messages";

export function CanvasFileDiff({ editedFile }: { editedFile: EditedFile }) {
  return (
    <div className="h-full overflow-auto bg-background p-4">
      {editedFile.hunks.length ? (
        editedFile.hunks.map((hunk, index) => (
          <div key={`${hunk.header}-${index}`} className="mb-4 last:mb-0">
            <div className="mb-1 font-mono text-xs text-muted-foreground">
              {hunk.header}
            </div>
            <pre className="overflow-x-auto rounded-md border border-border/50 bg-muted/20 p-3 font-mono text-xs leading-5">
              {hunk.lines.map((line, lineIndex) => (
                <span
                  key={`${lineIndex}-${line}`}
                  className={
                    line.startsWith("+")
                      ? "text-git-addition"
                      : line.startsWith("-")
                        ? "text-destructive"
                        : "text-foreground/80"
                  }
                >
                  {line}
                  {"\n"}
                </span>
              ))}
            </pre>
          </div>
        ))
      ) : (
        <p className="text-sm text-muted-foreground">
          {m.chat_edited_files_diff_empty()}
        </p>
      )}
    </div>
  );
}

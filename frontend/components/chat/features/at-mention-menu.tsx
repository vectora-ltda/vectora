"use client";

/**
 * AtMentionMenu
 *
 * Popup de seleção de arquivo/pasta disparado ao digitar `@` no input do chat.
 * Ao selecionar um arquivo, o token `@path` é inserido no input e o conteúdo
 * do arquivo é injetado no contexto da mensagem no envio.
 * Ao selecionar uma pasta, o browser navega para dentro dela (o `@query`
 * atualiza para `@pasta/` e o menu re-abre mostrando o conteúdo da pasta).
 *
 * Inspirado na UX do Claude Code e VS Code.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, File, FolderClosed, Loader2 } from "lucide-react";

import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";

interface Entry {
  name: string;
  path: string;
  kind: "dir" | "file";
}

/** Detecta um `@mention` ativo no final do input.
 *  Retorna `{ query, startIdx }` ou null quando não há trigger ativo. */
export function detectAtMention(
  input: string,
): { query: string; startIdx: number } | null {
  // Só ativa quando @ está no final — sem espaços depois (token em edição).
  const match = input.match(/@([^\s@]*)$/);
  if (!match) return null;
  return {
    query: match[1],
    startIdx: input.length - match[0].length,
  };
}

interface AtMentionMenuProps {
  input: string;
  onSelect: (path: string, startIdx: number, endIdx: number) => void;
}

export function AtMentionMenu({ input, onSelect }: AtMentionMenuProps) {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";

  const mention = useMemo(() => detectAtMention(input), [input]);

  // Diretório base da query (ex.: "src/comp" → dir "src", filtro "comp")
  const dirQuery = useMemo(() => {
    if (!mention) return null;
    const slash = mention.query.lastIndexOf("/");
    return slash >= 0 ? mention.query.slice(0, slash) : "";
  }, [mention]);

  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const lastFetchedDir = useRef<string | null>(null);

  useEffect(() => {
    if (dirQuery === null || !wsId) {
      // Sincroniza com o filesystem remoto do workspace — busca de dados,
      // não estado derivado de prop.
      // oxlint-disable-next-line react/set-state-in-effect
      setEntries(null);
      lastFetchedDir.current = null;
      return;
    }
    if (lastFetchedDir.current === dirQuery) return;
    lastFetchedDir.current = dirQuery;

    let cancelled = false;
    setLoading(true);
    setEntries(null);

    const qs = new URLSearchParams({ path: dirQuery });
    fetch(`/workspaces/${encodeURIComponent(wsId)}/tree?${qs}`)
      .then((r) => (r.ok ? r.json() : { entries: [] }))
      .then((data) => {
        if (!cancelled) setEntries(data?.entries ?? []);
      })
      .catch(() => {
        if (!cancelled) setEntries([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [wsId, dirQuery]);

  if (!mention || !wsId) return null;

  const slash = mention.query.lastIndexOf("/");
  const fileFilter =
    slash >= 0
      ? mention.query.slice(slash + 1).toLowerCase()
      : mention.query.toLowerCase();

  const visible = (entries ?? [])
    .filter((e) => !fileFilter || e.name.toLowerCase().startsWith(fileFilter))
    .slice(0, 12);

  if (!loading && visible.length === 0 && entries !== null) return null;

  const endIdx = mention.startIdx + 1 + mention.query.length;

  return (
    <div className="absolute bottom-full left-0 mb-2 w-80 rounded-lg border border-border bg-background shadow-xl py-1 z-50 animate-in fade-in slide-in-from-bottom-2 max-h-72 overflow-y-auto">
      <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5 sticky top-0 bg-background border-b border-border/40">
        <span className="font-mono">@</span>
        {m.at_title()}
      </div>

      {loading && (
        <div className="px-3 py-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="w-3 h-3 animate-spin" />…
        </div>
      )}

      {visible.map((entry) => (
        <button
          key={entry.path}
          type="button"
          onMouseDown={(e) => {
            e.preventDefault();
            if (entry.kind === "dir") {
              // Navegar para dentro da pasta: atualiza o @query com o novo prefixo
              const prefix = dirQuery
                ? `${dirQuery}/${entry.name}/`
                : `${entry.name}/`;
              onSelect(prefix, mention.startIdx, endIdx);
            } else {
              onSelect(entry.path, mention.startIdx, endIdx);
            }
          }}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-accent text-left transition-colors"
        >
          {entry.kind === "dir" ? (
            <FolderClosed className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <File className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="flex-1 truncate font-mono text-xs">
            {entry.name}
          </span>
          {entry.kind === "dir" && (
            <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground/60" />
          )}
        </button>
      ))}
    </div>
  );
}

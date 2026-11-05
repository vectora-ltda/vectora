"use client";

/**
 * RagCitationPopover — renderiza referências [1][2] em mensagens do assistente
 * com popover que exibe fonte e trecho do documento RAG.
 *
 * Uso: chame `renderWithCitations(content, citations)` para substituir os
 * marcadores `[N]` por elementos interativos.
 */

import { useState, type ReactNode } from "react";

export interface RagCitation {
  index: number;
  source: string;
  chunk: string;
}

// ---------------------------------------------------------------------------
// Popover inline (sem Radix — usa estado simples para manter KISS)
// ---------------------------------------------------------------------------

function CitationBadge({ citation }: { citation: RagCitation }) {
  const [open, setOpen] = useState(false);

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/15 hover:bg-primary/30 text-primary text-[9px] font-bold align-super leading-none transition-colors mx-0.5 cursor-pointer"
        aria-label={`Fonte ${citation.index}: ${citation.source}`}
      >
        {citation.index}
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <span
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          {/* Popover */}
          <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50 w-64 rounded-lg border border-border bg-card shadow-lg p-3 text-left">
            <span className="block text-[10px] font-semibold text-primary mb-1 truncate">
              [{citation.index}] {citation.source || "Fonte desconhecida"}
            </span>
            {citation.chunk && (
              <span className="block text-[10px] text-muted-foreground leading-relaxed line-clamp-4">
                {citation.chunk}
              </span>
            )}
          </span>
        </>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers para renderizar texto com referências [N] substituídas
// ---------------------------------------------------------------------------

/**
 * Transforma texto com marcadores `[N]` em nós React com `CitationBadge`
 * para cada referência encontrada nas citações fornecidas.
 *
 * Retorna o texto original quando não há citações ou nenhum marcador.
 */
export function renderWithCitations(
  text: string,
  citations: RagCitation[] | undefined,
): ReactNode {
  if (!citations || citations.length === 0) return text;

  const citationMap = new Map(citations.map((c) => [c.index, c]));
  const parts: ReactNode[] = [];
  let last = 0;
  // Matches [1], [2], ..., [99]
  const RE = /\[(\d{1,2})\]/g;
  let match: RegExpExecArray | null;

  while ((match = RE.exec(text)) !== null) {
    const idx = parseInt(match[1]!, 10);
    const citation = citationMap.get(idx);
    if (!citation) continue;

    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    parts.push(
      <CitationBadge key={`cite-${idx}-${match.index}`} citation={citation} />,
    );
    last = match.index + match[0].length;
  }

  if (last < text.length) parts.push(text.slice(last));

  return parts.length > 0 ? <>{parts}</> : text;
}

"""Text Processing Service — Tokenização e Chunking.

Fonte única de verdade para splitting de documentos e contagem de tokens.
Qualquer mudança de estratégia (encoding, chunk_size, overlap) acontece aqui,
não espalhada por tools ou nodes.

Design:
- TextService é um singleton leve: criado uma vez, reutilizado em todo o processo.
- O splitter usa tiktoken (cl100k_base) e um split recursivo por separadores
  (``["\\n\\n", "\\n", " ", ""]``), sem dependência externa além do tiktoken.
- O token_counter usa o mesmo encoding para consistência com o splitter:
  trim_messages() e ingest_docs() falam a mesma língua de tokens.

Usage:
    from backend.services.text import text_service

    chunks = text_service.split(long_text)
    n_tokens = text_service.count_tokens("hello world")
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import tiktoken

logger = logging.getLogger(__name__)

#: Separadores do split recursivo — mesmo conjunto que o
#: ``RecursiveCharacterTextSplitter`` usava (do mais grosseiro ao mais fino).
_SEPARATORS = ("\n\n", "\n", " ", "")


def _split_with_separator(text: str, separator: str) -> list[str]:
    """Divide por `separator`; `""` divide por caractere."""
    if separator:
        return text.split(separator)
    return list(text)


def _join(parts: list[str], separator: str) -> str:
    return separator.join(parts)


def _merge_splits(
    splits: list[str],
    separator: str,
    *,
    token_len: Callable[[str], int],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Junta splits consecutivos em chunks ≤ ``chunk_size`` tokens, com
    ``chunk_overlap`` tokens de sobreposição entre chunks adjacentes."""
    docs: list[str] = []
    current: list[str] = []
    total = 0
    sep_len = token_len(separator)

    for piece in splits:
        piece_len = token_len(piece)
        overhead = sep_len if current else 0
        if total + piece_len + overhead > chunk_size:
            if current:
                docs.append(_join(current, separator))
                # Encolhe `current` do início até caber no overlap.
                while current and (
                    total > chunk_overlap or total + piece_len > chunk_size
                ):
                    removed = current.pop(0)
                    total -= token_len(removed) + (sep_len if current else 0)
        if piece_len > chunk_size:
            # Piece único que sozinho excede o teto — anexa do jeito que está
            # (sem conseguir respeitar o limite; é um chunk indivisível).
            if current:
                docs.append(_join(current, separator))
                current = []
                total = 0
            docs.append(piece)
            continue
        current.append(piece)
        total += piece_len + (sep_len if len(current) > 1 else 0)

    if current:
        docs.append(_join(current, separator))
    return docs


def _recursive_split(
    text: str,
    separators: list[str],
    *,
    token_len: Callable[[str], int],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    final: list[str] = []

    # Escolhe o primeiro separador presente no texto (fallback: o último).
    separator = separators[-1]
    next_separators: list[str] = []
    for i, sep in enumerate(separators):
        if sep == "" or sep in text:
            separator = sep
            next_separators = separators[i + 1 :]
            break

    splits = _split_with_separator(text, separator)
    good: list[str] = []
    for piece in splits:
        if token_len(piece) < chunk_size:
            good.append(piece)
        else:
            if good:
                final.extend(
                    _merge_splits(
                        good,
                        separator,
                        token_len=token_len,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                    )
                )
                good = []
            if not next_separators:
                final.append(piece)
            else:
                final.extend(
                    _recursive_split(
                        piece,
                        next_separators,
                        token_len=token_len,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                    )
                )

    if good:
        final.extend(
            _merge_splits(
                good,
                separator,
                token_len=token_len,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return final


class TextService:
    """Serviço de tokenização e chunking de documentos.

    Centraliza toda lógica de splitting para que:
    - tools/rag.py (ingestão) e nodes/engine.py (trim_messages) usem
      o MESMO encoding e as mesmas regras de contagem.
    - Mudanças de estratégia (encoding, chunk_size, overlap) ocorram
      em um único lugar — settings.py → TextService.

    Attributes:
        encoding_name: Nome do encoding tiktoken (ex: cl100k_base).
        chunk_size: Tamanho máximo de chunk em tokens.
        chunk_overlap: Sobreposição entre chunks em tokens.
    """

    def __init__(
        self,
        encoding_name: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self.encoding_name = encoding_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Encoding tiktoken — carregado uma vez, cacheado pelo próprio tiktoken
        self._enc = tiktoken.get_encoding(encoding_name)

        logger.debug(
            "TextService initialized",
            extra={
                "encoding": encoding_name,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            },
        )

    def _token_len(self, text: str) -> int:
        return len(self._enc.encode(text))

    # ── Chunking ─────────────────────────────────────────────────────────────

    def split(self, text: str) -> list[str]:
        """Divide texto em chunks respeitando limites de tokens.

        Returns:
            Lista de chunks, cada um com no máximo ``chunk_size`` tokens.
        """
        return _recursive_split(
            text,
            list(_SEPARATORS),
            token_len=self._token_len,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

    # ── Token counting ────────────────────────────────────────────────────────

    def count_tokens(self, text: str) -> int:
        """Conta tokens em uma string usando tiktoken."""
        return self._token_len(text)


# ── Singleton ─────────────────────────────────────────────────────────────────
# Criado uma vez na importação do módulo, guiado pelos Settings.
# Importar de outros módulos: `from backend.services.text import text_service`


def _build() -> TextService:
    from backend.settings import settings

    return TextService(
        encoding_name=settings.tiktoken_encoding,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )


text_service: TextService = _build()

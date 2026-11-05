"""Coleta e parsing de arquivos de contexto do workspace.

Lê e interpreta arquivos de instrução do projeto (AGENTS.md, CLAUDE.md,
GEMINI.md, VECTORA.md) e quaisquer markdown em .vectora/ com suporte completo
a frontmatter YAML — incluindo todos os campos Paperclip.

Campos Paperclip reconhecidos:
  title        — nome de exibição (default: stem do arquivo)
  description  — descrição curta do propósito do arquivo
  type         — context | system | instruction | memory | tool_instruction
  weight       — int (maior = injetado primeiro, default 0)
  enabled      — bool (false = ignorado completamente, default true)
  inject_when  — always | on_request | conditional (default "always")
  condition    — string (reservado para avaliação futura; ignorado agora)
  tags         — lista de strings
  truncate_at  — int (limite de chars para o corpo deste arquivo)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Nomes de arquivos de instrução reconhecidos na raiz do workspace.
_ROOT_INSTRUCTION_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "VECTORA.md",
    "SYSTEM.md",
    "COPILOT-INSTRUCTIONS.md",
)

# Arquivos da pasta .vectora/ que NÃO são contexto livre (geridos internamente).
_VECTORA_EXCLUDED_NAMES = frozenset(
    {"MANIFEST.md", "GRAPH_REPORT.md", "build_status.json"}
)

# Subpastas de .vectora/ que nunca contêm contexto de projeto.
_VECTORA_EXCLUDED_DIRS = frozenset({"graph", "buckets", "index", "cache"})

# Limite default de chars por arquivo injetado no contexto.
_DEFAULT_TRUNCATE = 4000


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extrai e parseia frontmatter YAML de um arquivo Markdown.

    Retorna (meta_dict, body_sem_frontmatter).
    Se não houver frontmatter ou o YAML for inválido, retorna ({}, content).
    """
    if not content.startswith("---"):
        return {}, content

    # Procura o segundo delimitador "---"
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    yaml_block = content[3:end].strip()
    body = content[end + 3 :].lstrip("\n")

    try:
        import yaml  # type: ignore[import-untyped]

        parsed = yaml.safe_load(yaml_block)
        if not isinstance(parsed, dict):
            return {}, content
        return parsed, body
    except Exception:
        logger.debug("Falha ao parsear frontmatter YAML", exc_info=True)
        return {}, content


@dataclass
class ContextFile:
    """Arquivo de contexto com frontmatter parseado e corpo processado."""

    path: Path
    title: str
    description: str
    type: str
    weight: int
    enabled: bool
    inject_when: str
    condition: str
    tags: list[str]
    body: str
    frontmatter: dict = field(default_factory=dict)

    @classmethod
    def from_path(cls, path: Path) -> ContextFile:
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw = ""

        meta, body = parse_frontmatter(raw)

        # Aplica truncate_at ao corpo se especificado no frontmatter.
        truncate_at = int(meta.get("truncate_at", _DEFAULT_TRUNCATE))
        if len(body) > truncate_at:
            body = body[:truncate_at] + "\n\n[... truncado ...]"

        from backend.services.prompt_injection import detect_injection

        matched = detect_injection(body)
        if matched is not None:
            logger.warning(
                "context_files: padrão de prompt injection bloqueado",
                extra={"path": str(path), "pattern": matched},
            )
            body = (
                f"[BLOCKED: conteúdo de {path.name} continha um padrão de "
                f"possível prompt injection ({matched}) — bloqueado por "
                "segurança e não foi injetado no contexto do agente.]"
            )

        title = str(meta.get("title") or "") or path.stem
        description = str(meta.get("description") or "")
        file_type = str(meta.get("type") or "context")

        raw_weight = meta.get("weight", 0)
        try:
            weight = int(raw_weight)
        except (TypeError, ValueError):
            weight = 0

        enabled_raw = meta.get("enabled", True)
        enabled = (
            bool(enabled_raw) if not isinstance(enabled_raw, bool) else enabled_raw
        )

        inject_when = str(meta.get("inject_when") or "always")
        condition = str(meta.get("condition") or "")

        tags_raw = meta.get("tags", [])
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t) for t in tags_raw]
        else:
            tags = []

        return cls(
            path=path,
            title=title,
            description=description,
            type=file_type,
            weight=weight,
            enabled=enabled,
            inject_when=inject_when,
            condition=condition,
            tags=tags,
            body=body,
            frontmatter=meta,
        )


def collect_context_files(cwd: str) -> list[ContextFile]:
    """Coleta todos os arquivos de contexto do workspace.

    Ordem de coleta (antes de ordenar por weight):
    1. Arquivos de instrução na raiz: AGENTS.md, CLAUDE.md, GEMINI.md, VECTORA.md, …
    2. Todos os .md em .vectora/ (exceto subpastas reservadas e MANIFEST.md)

    Arquivos com enabled=false são removidos.
    Resultado ordenado por weight DESC (maior weight = mais prioritário).
    """
    root = Path(cwd)
    if not root.exists():
        return []

    collected: list[ContextFile] = []

    # 1. Arquivos de instrução na raiz do workspace.
    for name in _ROOT_INSTRUCTION_FILES:
        p = root / name
        if p.exists() and p.is_file():
            cf = ContextFile.from_path(p)
            if cf.enabled:
                collected.append(cf)

    # 2. Arquivos markdown em .vectora/ (nível 1, sem recursão em subpastas reservadas).
    vectora_dir = root / ".vectora"
    if vectora_dir.is_dir():
        for p in sorted(vectora_dir.iterdir()):
            # Pula subpastas reservadas.
            if p.is_dir() and p.name in _VECTORA_EXCLUDED_DIRS:
                continue
            # Só arquivos .md diretos em .vectora/ (não recursivo nos subdiretórios).
            if p.is_file() and p.suffix.lower() == ".md":
                if p.name in _VECTORA_EXCLUDED_NAMES:
                    continue
                cf = ContextFile.from_path(p)
                if cf.enabled:
                    collected.append(cf)

    # Ordena por weight DESC (maior = mais prioritário); estável preserva inserção.
    collected.sort(key=lambda f: f.weight, reverse=True)
    return collected


def format_context_files_for_prompt(
    files: list[ContextFile],
    *,
    include_on_request: bool = False,
    max_total_chars: int = 12_000,
) -> str:
    """Formata a lista de ContextFile como texto para injetar no prompt.

    Por padrão exclui arquivos com inject_when='on_request'.
    O total é limitado a max_total_chars para não inflar o contexto.
    """
    sections: list[str] = []
    total = 0

    for cf in files:
        if cf.inject_when == "on_request" and not include_on_request:
            continue
        header = f"## {cf.title} ({cf.path.name})"
        if cf.description:
            header += f"\n_{cf.description}_"
        block = f"{header}\n\n{cf.body}"
        if total + len(block) > max_total_chars:
            remaining = max_total_chars - total
            if remaining > 200:
                block = block[:remaining] + "\n\n[... truncado ...]"
            else:
                break
        sections.append(block)
        total += len(block)

    return "\n\n---\n\n".join(sections)

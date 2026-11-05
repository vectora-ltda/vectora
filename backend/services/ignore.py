"""Utilitários de filtragem baseada em .gitignore e .vectoraignore via pathspec.

Módulo compartilhado entre fs.py (grep, list_dir) e rag.py (ingest_docs)
para garantir que nenhuma tool do Vectora indexe ou varra arquivos que
estejam no .gitignore ou .vectoraignore do projeto.

.vectoraignore — controle dedicado para o Vectora:
  - Mesmo formato gitwildmatch do .gitignore
  - Colocado na raiz do projeto (ou qualquer ancestral)
  - Combinado com .gitignore: ambos são respeitados simultaneamente
  - Útil para excluir arquivos que não estão no .gitignore mas que
    não devem ser indexados no RAG (ex: fixtures de teste volumosos,
    dados gerados, arquivos de benchmark, docs temporários)

Usa a lib pathspec com suporte a gitwildmatch — o mesmo formato do git.
"""

from pathlib import Path

import pathspec

# Diretórios e extensões sempre ignorados, independente de .gitignore
ALWAYS_SKIP_SUFFIXES: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".o", ".exe", ".dll", ".so", ".class", ".pyd", ".whl"}
)

ALWAYS_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".egg-info",
        "dist",
        "build",
        ".build",
        ".tox",
        # AI assistant config dirs — never index for RAG
        ".claude",
        ".anthropic",
    }
)

#: A pasta `.vectora` (planos/memória do Vectora) é sempre acessível — a
#: obediência ao `.gitignore` nunca pode escondê-la ou bloquear o acesso,
#: mesmo que `.vectora/` esteja listada no `.gitignore` do projeto. Ela é o
#: estado de runtime do próprio Vectora (plans/memory), não artefato de build
#: ou dependência a ignorar.
ALWAYS_ALLOW_DIRS: frozenset[str] = frozenset({".vectora"})


def load_vectoraignore_spec(base_dir: Path) -> pathspec.PathSpec | None:
    """Carrega o .vectoraignore mais próximo e retorna um PathSpec gitwildmatch.

    Mesmo comportamento de busca do ``load_gitignore_spec``: sobe a árvore de
    diretórios a partir de ``base_dir`` até encontrar um ``.vectoraignore``.

    O ``.vectoraignore`` usa exatamente o mesmo formato do ``.gitignore`` —
    padrões gitwildmatch como ``*.log``, ``data/**``, ``!important.txt``.

    Args:
        base_dir: Diretório de partida para a busca.

    Returns:
        PathSpec pronto para uso em ``match_file()``, ou None se não houver
        nenhum ``.vectoraignore`` acessível.
    """
    for parent in [base_dir.resolve(), *base_dir.resolve().parents]:
        vectoraignore = parent / ".vectoraignore"
        if vectoraignore.is_file():
            try:
                patterns = vectoraignore.read_text(encoding="utf-8", errors="ignore")
                return pathspec.PathSpec.from_lines("gitignore", patterns.splitlines())
            except Exception:
                return None
    return None


def load_ignore_spec(base_dir: Path) -> pathspec.PathSpec | None:
    """Combina .gitignore + .vectoraignore num único PathSpec gitwildmatch.

    Cada arquivo é procurado independentemente subindo a árvore de diretórios.
    Os padrões dos dois são mesclados: um arquivo é ignorado se bater com
    qualquer padrão de qualquer um dos dois arquivos.

    Esta é a função a usar nos call-sites principais (``ingest_docs``,
    ``grep``, ``list_dir``) — substitui chamadas diretas a
    ``load_gitignore_spec`` quando se quer o comportamento completo.

    Args:
        base_dir: Diretório de partida para a busca de ambos os arquivos.

    Returns:
        PathSpec combinado, ou None se nenhum dos dois arquivos existir.
    """
    all_lines: list[str] = []

    for filename in (".gitignore", ".vectoraignore"):
        for parent in [base_dir.resolve(), *base_dir.resolve().parents]:
            candidate = parent / filename
            if candidate.is_file():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
                    all_lines.extend(content.splitlines())
                except Exception:
                    pass
                break  # encontrou o mais próximo; para de subir para este arquivo

    if not all_lines:
        return None
    return pathspec.PathSpec.from_lines("gitignore", all_lines)


def load_gitignore_spec(base_dir: Path) -> pathspec.PathSpec | None:
    """Carrega o .gitignore mais próximo e retorna um PathSpec gitwildmatch.

    Sobe a árvore de diretórios a partir de ``base_dir`` até encontrar um
    ``.gitignore``. Para na raiz do filesystem se não encontrar nenhum.

    Args:
        base_dir: Diretório de partida para a busca (geralmente o dir do projeto).

    Returns:
        PathSpec pronto para uso em ``match_file()``, ou None se não houver
        nenhum ``.gitignore`` acessível.
    """
    for parent in [base_dir.resolve(), *base_dir.resolve().parents]:
        gitignore = parent / ".gitignore"
        if gitignore.is_file():
            try:
                patterns = gitignore.read_text(encoding="utf-8", errors="ignore")
                return pathspec.PathSpec.from_lines("gitignore", patterns.splitlines())
            except Exception:
                return None
    return None


def is_ignored(path: Path, base_dir: Path, spec: pathspec.PathSpec | None) -> bool:
    """Retorna True se ``path`` deve ser ignorado.

    Três camadas de verificação, em ordem crescente de custo:

    1. Algum componente do path está em ALWAYS_SKIP_DIRS
       (ex: ``__pycache__``, ``.git``, ``node_modules``)
    2. A extensão do arquivo está em ALWAYS_SKIP_SUFFIXES
       (ex: ``.pyc``, ``.exe``)
    3. O path relativo ao ``base_dir`` bate com um padrão do ``.gitignore``
       carregado no PathSpec (ex: ``*.log``, ``dist/``, ``.env``)

    Args:
        path: Caminho absoluto ou relativo a verificar.
        base_dir: Diretório raiz usado para calcular o caminho relativo
                  ao avaliar o PathSpec.
        spec: PathSpec retornado por ``load_gitignore_spec()``.
              Pode ser None se não houver .gitignore.

    Returns:
        True se o arquivo deve ser ignorado, False caso contrário.
    """
    # Camada 0 — exceção do estado do Vectora: `.vectora` (plans/memory) é
    # sempre acessível, nunca ignorada por .gitignore/.vectoraignore.
    if any(part in ALWAYS_ALLOW_DIRS for part in path.parts):
        return False

    # Camada 1 — dirs hardcoded (mais rápido, sem I/O)
    for part in path.parts:
        if part in ALWAYS_SKIP_DIRS:
            return True

    # Camada 2 — extensões binárias/compiladas
    if path.suffix in ALWAYS_SKIP_SUFFIXES:
        return True

    # Camada 3 — regras do .gitignore / .vectoraignore via pathspec
    # ``spec`` pode ser um PathSpec isolado (load_gitignore_spec) ou
    # combinado (load_ignore_spec). A função não precisa saber a origem.
    if spec is not None:
        try:
            rel = path.relative_to(base_dir)
            # pathspec espera separador POSIX em qualquer plataforma
            if spec.match_file(str(rel).replace("\\", "/")):
                return True
        except ValueError:
            pass

    return False


def walk_files(
    base_dir: Path,
    glob_pattern: str = "**/*",
    spec: pathspec.PathSpec | None = None,
    *,
    include_dirs: bool = False,
) -> tuple[list[Path], int]:
    """Varre ``base_dir`` com poda de diretórios e retorna (entradas, ignorados).

    Substitui ``Path.rglob()`` puro em contextos que precisam de filtragem.

    A varredura usa ``os.walk`` com **poda de diretórios durante o walk** —
    ``rglob`` puro entra em ``node_modules``/``.venv`` inteiros antes de
    filtrar, o que em repositórios JS/Python grandes leva minutos (era a
    causa da suite de testes "travar": cada teste do orchestrator varria a
    árvore completa do repo 3x).

    Args:
        base_dir: Diretório raiz da varredura.
        glob_pattern: Padrão glob aplicado aos nomes de arquivo
            (ex: ``**/*.md``, ``**/*.py``). Não se aplica a diretórios.
        spec: PathSpec do .gitignore (None → sem filtragem por gitignore,
              mas ALWAYS_SKIP ainda se aplica).
        include_dirs: Se True, inclui também os diretórios não podados no
            resultado (necessário para listagens recursivas tipo list_dir).

    Returns:
        Tupla (entradas ordenadas, skipped_ignored). O contador soma cada
        diretório podado (ALWAYS_SKIP_DIRS ou gitignore — a subárvore inteira
        conta como 1, já que não é varrida) e cada arquivo que bateu no glob
        mas foi ignorado pelo spec.
    """
    import fnmatch
    import os

    # Remove leading "**/" prefix so the filename match receives a plain
    # pattern (e.g. "*.md")
    stripped = glob_pattern
    while stripped.startswith("**/"):
        stripped = stripped[3:]

    results: list[Path] = []
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(base_dir):
        current = Path(dirpath)
        # Poda in-place: os.walk não desce em dirs removidos de dirnames.
        kept: list[str] = []
        for d in dirnames:
            # `.vectora` (estado do Vectora) e tudo que está DENTRO dela é
            # sempre varrido — nunca podado por .gitignore/.vectoraignore,
            # mesmo que `.vectora/` esteja listada neles. Cobre tanto o
            # diretório `.vectora` em si quanto os subdirs (plans/, memory/)
            # que ficam por baixo dele.
            if d in ALWAYS_ALLOW_DIRS or ".vectora" in current.parts:
                kept.append(d)
                if include_dirs:
                    results.append(current / d)
                continue
            if d in ALWAYS_SKIP_DIRS:
                skipped += 1
                continue
            if spec is not None:
                rel = (current / d).relative_to(base_dir)
                # Diretório ignorado pelo gitignore → não desce nele.
                if spec.match_file(str(rel).replace("\\", "/") + "/"):
                    skipped += 1
                    continue
            kept.append(d)
            if include_dirs:
                results.append(current / d)
        dirnames[:] = kept
        for filename in filenames:
            if not fnmatch.fnmatch(filename, stripped):
                continue
            path = current / filename
            if is_ignored(path, base_dir, spec):
                skipped += 1
            else:
                results.append(path)
    return sorted(results), skipped


def iter_files(
    base_dir: Path,
    glob_pattern: str = "**/*",
    spec: pathspec.PathSpec | None = None,
) -> list[Path]:
    """Lista arquivos em ``base_dir`` respeitando .gitignore.

    Wrapper de conveniência sobre ``walk_files`` para quem só precisa dos
    arquivos, sem o contador de ignorados nem diretórios.

    Args:
        base_dir: Diretório raiz da varredura.
        glob_pattern: Padrão glob (ex: ``**/*.md``, ``**/*.py``).
        spec: PathSpec do .gitignore (None → sem filtragem por gitignore,
              mas ALWAYS_SKIP ainda se aplica).

    Returns:
        Lista de Paths de arquivos que passaram em todos os filtros.
    """
    files, _ = walk_files(base_dir, glob_pattern, spec)
    return files

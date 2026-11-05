"""Debug Dump Generator - Cria arquivo .tar.gz com estado completo para auditoria de QA.

Ferramenta crítica para que testers reportem bugs com contexto:
- Bancos de dados (vectora.db, embedding_queue.db)
- Logs (estruturados em JSON)
- Informações de sistema
- Timestamp e correlação
"""

import json
import logging
import platform
import sys
import tarfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from backend.settings import settings
from backend.version import __version__

logger = logging.getLogger(__name__)


async def generate_debug_dump(
    output_file: str | None = None,
    *,
    include_databases: bool = True,
    include_logs: bool = True,
) -> str:
    """Gera arquivo de debug dump para auditoria de QA.

    Cria um .tar.gz contendo:
    - Bancos de dados (vectora.db, embedding_queue.db)
    - Logs (logs/*.jsonl)
    - Metadados (Python version, platform, timestamp)
    - Info.json com contexto

    Args:
        output_file: Caminho do arquivo .tar.gz de saída.
                    Se None, gera automaticamente com timestamp.
        include_databases: Incluir bancos de dados (pode ser grande)
        include_logs: Incluir logs (essencial para debugging)

    Returns:
        Caminho do arquivo .tar.gz gerado

    Example:
        >>> path = await generate_debug_dump()
        >>> print(f"Debug dump: {path}")
        Debug dump: vectora_debug_2026-05-14T14-30-45.tar.gz
    """
    if output_file is None:
        timestamp = datetime.now(UTC).isoformat().replace(":", "-").split(".")[0]
        output_file = f"vectora_debug_{timestamp}.tar.gz"

    logger.info("Gerando debug dump", extra={"output_file": output_file})

    # Coletar metadados do sistema
    metadata = {
        "timestamp": datetime.now(UTC).isoformat(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "llm_provider": settings.get_llm_provider(),
        "include_databases": include_databases,
        "include_logs": include_logs,
    }

    # Criar arquivo .tar.gz
    try:
        with tarfile.open(output_file, "w:gz") as tar:
            # Adicionar metadados
            meta_json = json.dumps(metadata, indent=2).encode()
            meta_info = tarfile.TarInfo(name="INFO.json")
            meta_info.size = len(meta_json)
            tar.addfile(meta_info, BytesIO(meta_json))

            # Adicionar bancos de dados
            if include_databases:
                data_dir = settings.data_dir or (Path.home() / ".vectora" / "data")
                if data_dir.exists():
                    for db_file in data_dir.glob("*.db"):
                        tar.add(str(db_file), arcname=f"data/{db_file.name}")
                        logger.debug(f"Adicionado: {db_file.name}")

                    for wal_file in data_dir.glob("*.db-*"):
                        tar.add(str(wal_file), arcname=f"data/{wal_file.name}")

            # Adicionar logs
            if include_logs:
                log_dir = settings.logs_dir or (Path.home() / ".vectora" / "logs")
                if log_dir.exists():
                    for log_file in log_dir.glob("*.jsonl"):
                        tar.add(str(log_file), arcname=f"logs/{log_file.name}")
                        logger.debug(f"Adicionado: {log_file.name}")

                    for log_file in log_dir.glob("*.log"):
                        tar.add(str(log_file), arcname=f"logs/{log_file.name}")

            # Adicionar .env (sem secrets, apenas configuração)
            env_file = settings.env_file or (Path.home() / ".vectora" / ".env")
            if env_file.exists():
                # Ler .env e remover linhas com "KEY", "SECRET", "TOKEN"
                with env_file.open() as f:
                    lines = f.readlines()

                safe_env = [
                    line
                    for line in lines
                    if not any(
                        secret in line.upper()
                        for secret in ["KEY", "SECRET", "TOKEN", "PASSWORD"]
                    )
                ]

                safe_env_content = "".join(safe_env).encode()
                env_info = tarfile.TarInfo(name=".env.safe")
                env_info.size = len(safe_env_content)
                tar.addfile(env_info, BytesIO(safe_env_content))
                logger.debug("Adicionado: .env (sem secrets)")

        size_mb = Path(output_file).stat().st_size / (1024 * 1024)
        logger.info(
            f"✅ Debug dump gerado com sucesso: {output_file} ({size_mb:.2f} MB)"
        )

        return output_file

    except Exception as e:
        logger.exception(f"Erro ao gerar debug dump: {e}")
        raise


async def create_qa_report(
    debug_dump_path: str,
    tester_name: str,
    test_scenario: str,
    bug_description: str,
    severity: str = "medium",
) -> str:
    """Cria relatório estruturado de bug para QA.

    Args:
        debug_dump_path: Caminho do arquivo debug dump
        tester_name: Nome do tester
        test_scenario: Cenário que estava testando
        bug_description: Descrição detalhada do bug
        severity: "critical" | "high" | "medium" | "low"

    Returns:
        Conteúdo do relatório (markdown)
    """
    return f"""# 🐛 Relatório de Bug - Vectora v{__version__}

## Informações do Teste

**Tester:** {tester_name}
**Timestamp:** {datetime.now(UTC).isoformat()}
**Cenário:** {test_scenario}
**Severidade:** {severity.upper()}

## Descrição do Bug

{bug_description}

## Debug Dump

**Arquivo:** `{debug_dump_path}`
**Tamanho:** {Path(debug_dump_path).stat().st_size / 1024:.2f} KB

### Conteúdo do Dump

- `INFO.json` - Metadados de sistema (Python, platform, config)
- `data/*.db` - Bancos de dados (vectora.db, embedding_queue.db)
- `data/*.db-wal` - WAL files para recuperação
- `logs/*.jsonl` - Logs estruturados (com correlation_id)
- `.env.safe` - Configuração (sem secrets)

## Instruções para Reproduzir

[Por favor, descreva os passos para reproduzir o bug]

1.
2.
3.

## Logs Relevantes

[Cole aqui as linhas relevantes de `logs/mcp.log` com correlation_id]

```
[correlation_id] EVENT_NAME: ...
[correlation_id] EVENT_NAME: ...
```

---

**Status:** Aberto
**Assignee:** [Será atribuído pelo time de desenvolvimento]
"""


# CLI Interface
if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        """CLI para gerar debug dumps."""
        dump_path = await generate_debug_dump()
        print(f"✅ Debug dump: {dump_path}")
        print("\nPróximos passos:")
        print("1. Descreva o bug encontrado")
        print("2. Envie este arquivo junto com a descrição")
        print("3. Copie o conteúdo do correlation_id dos logs para rastreabilidade")

    asyncio.run(main())

"""Loop RPC leve que roda DENTRO do jail (`backend.sandbox.workspace_jail`
o spawna via ``python -m backend.sandbox.worker``, envolto em ``bwrap``).

Todo I/O feito aqui já está confinado pelo mount namespace do bwrap que
envolve este processo — o worker não reimplementa isolamento nenhum, só
expõe uma interface de RPC (JSON-lines por stdin/stdout, sem framework)
pro processo backend, fora do jail, delegar exec/read/write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from backend.sandbox.landlock import apply_landlock
from backend.sandbox.rlimits import apply_rlimits

logger = logging.getLogger(__name__)


async def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    op = req.get("op")
    req_id = req.get("id")
    try:
        if op == "exec":
            proc = await asyncio.create_subprocess_exec(
                *req["command"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
            return {
                "id": req_id,
                "stdout": stdout_b.decode("utf-8", errors="replace"),
                "stderr": stderr_b.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode or 0,
            }
        if op == "read_file":
            content = Path(req["path"]).read_text(encoding="utf-8")
            return {"id": req_id, "content": content}
        if op == "write_file":
            target = Path(req["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(req["content"], encoding="utf-8")
            return {"id": req_id, "ok": True}
        return {"id": req_id, "error": f"op desconhecida: {op!r}"}
    except Exception as exc:
        return {"id": req_id, "error": str(exc)}


async def main() -> None:
    # Aplicado antes do loop RPC — todo `exec` subsequente (op="exec") herda
    # os limites, contendo fork bombs/exaustão de descritores mesmo sem
    # Landlock/seccomp cobrirem esse vetor. Perfil vem de env var setada por
    # `workspace_jail.py` no spawn (`--setenv`, conforme `policy.lockdown`).
    apply_rlimits(lockdown=os.environ.get("VECTORA_SANDBOX_LOCKDOWN") == "1")

    # Landlock (4.1) — defesa em profundidade complementar aos binds do
    # bwrap. Só roda se o worker foi spawnado via workspace_jail.py (que
    # sempre seta VECTORA_SANDBOX_WORKSPACE_DIR); ausência da env var
    # (ex. worker chamado direto em teste) pula sem tentar.
    workspace_dir = os.environ.get("VECTORA_SANDBOX_WORKSPACE_DIR")
    if workspace_dir:
        rw_paths = [
            workspace_dir,
            *json.loads(os.environ.get("VECTORA_SANDBOX_RW_PATHS", "[]")),
        ]
        ro_paths = json.loads(os.environ.get("VECTORA_SANDBOX_RO_PATHS", "[]"))
        allow_tcp_ports = tuple(
            json.loads(os.environ.get("VECTORA_SANDBOX_ALLOW_TCP_PORTS", "[]"))
        )
        apply_landlock(rw_paths, ro_paths, allow_tcp_ports)

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            req = json.loads(line)
        except Exception:
            logger.warning("sandbox worker: linha não é JSON válido, ignorando")
            continue
        resp = await handle_request(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())

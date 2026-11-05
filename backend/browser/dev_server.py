"""Resolve a URL do dev server local ativo de um workspace.

Usado só como fallback das tools de automação (`browser_click`,
`browser_screenshot`, etc.) quando nenhuma página foi navegada ainda via
`browser_navigate` — nesse caso a automação abre o dev server que o
workspace já subiu via `.vectora/launch.json`/`browser_start`.
"""

from __future__ import annotations


async def resolve_dev_server_url(workspace_id: str) -> str | None:
    """Retorna `http://localhost:{port}` do primeiro dev server rodando, ou None."""
    from backend.api.handlers.workspaces import (
        _browser_key,
        _browser_procs,
        _is_port_open,
        get_launch_json,
    )

    launch = await get_launch_json(workspace_id)
    for cfg in launch.configurations:
        proc = _browser_procs.get(_browser_key(workspace_id, cfg.name))
        if (
            proc is not None
            and proc.returncode is None
            and await _is_port_open("127.0.0.1", cfg.port)
        ):
            return f"http://localhost:{cfg.port}"
    return None

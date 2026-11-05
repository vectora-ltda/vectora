"""Minimal Python adapter used by :class:`backend.services.vext_host.VextHost`."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RpcRequest(BaseModel):
    """Validated JSON-RPC request received from an extension."""

    model_config = ConfigDict(extra="forbid")

    jsonrpc: str = Field(pattern=r"^2\.0$")
    id: int | str | None = None
    method: str = Field(min_length=1, max_length=512)
    params: dict[str, object] = Field(default_factory=dict)


class RpcResponse(BaseModel):
    """Validated JSON-RPC response emitted by the adapter."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: object | None = None
    error: str | None = None


def _load_handler(
    root: Path, entrypoint: str
) -> Callable[[str, dict[str, object]], object]:
    path = (root / entrypoint).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("entrypoint fora do diretório isolado")
    spec = importlib.util.spec_from_file_location("vectora_vext_entrypoint", path)
    if spec is None or spec.loader is None:
        raise ValueError("não foi possível carregar o entrypoint")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "handle", None)
    if not callable(handler):
        raise ValueError("entrypoint deve expor handle(method, params)")
    return handler


def run(root: Path, entrypoint: str) -> int:
    """Serve newline-delimited JSON-RPC messages until stdin closes."""
    try:
        handler = _load_handler(root, entrypoint)
    except Exception as exc:
        print(json.dumps({"jsonrpc": "2.0", "id": None, "error": str(exc)}), flush=True)
        return 1
    for line in sys.stdin:
        if len(line.encode("utf-8")) > 1 * 1024 * 1024:
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": "request too large"}
                ),
                flush=True,
            )
            continue
        request: RpcRequest | None = None
        try:
            request = RpcRequest.model_validate(json.loads(line))
            result = handler(request.method, request.params)
            response = RpcResponse(id=request.id, result=result)
            payload = response.model_dump(mode="json", exclude_none=True)
            payload.setdefault("result", None)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            continue
        except Exception as exc:
            response = RpcResponse(id=request.id if request else None, error=str(exc))
        print(response.model_dump_json(exclude_none=True), flush=True)
    return 0


def main() -> int:
    """Parse adapter arguments and serve the extension entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--entrypoint", required=True)
    args = parser.parse_args()
    return run(args.root, args.entrypoint)


if __name__ == "__main__":
    raise SystemExit(main())

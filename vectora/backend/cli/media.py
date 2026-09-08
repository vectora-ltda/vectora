"""Comandos ``vectora media`` sobre o adaptador nativo de mídia."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from backend.services.media_adapter import invoke_media, media_specs
from backend.tools.context import ToolContext


def _result(status: str, data: Any = None, error: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"schema_version": "1", "status": status, "data": data}
    if error is not None:
        value["error"] = error
    return value


def _print(value: dict[str, Any], output: str) -> int:
    if output == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif value["status"] == "error":
        print(f"Erro: {value.get('error', 'falha')}", file=sys.stderr)
    else:
        print(value.get("data", ""))
    return 0 if value["status"] == "ok" else 1


def run_media(args: Any) -> None:
    """List or invoke a media tool with a trusted execution context."""
    if args.action == "list":
        data = [
            {"name": spec.name, "description": spec.description}
            for spec in media_specs()
        ]
        raise SystemExit(_print(_result("ok", data), args.output))
    names = {
        "image": ("generate_image", {"prompt": args.prompt}),
        "speech": ("text_to_speech", {"text": args.text, "voice": args.voice}),
        "video": ("generate_video", {"prompt": args.prompt}),
        "analyze-video": (
            "analyze_video",
            {"path": args.path, "question": args.question},
        ),
    }
    name, arguments = names[args.action]
    context = ToolContext(
        user_id=args.user_id, model=args.model, thread_id=args.thread_id
    )
    value = asyncio.run(invoke_media(name, arguments, context))
    status = "error" if value.startswith("Error:") else "ok"
    raise SystemExit(
        _print(
            _result(status, value, value if status == "error" else None), args.output
        )
    )

"""Comandos ``vectora media`` sobre o adaptador nativo de mídia."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Protocol

from backend.services.media_adapter import invoke_media, media_specs
from backend.tools.context import ToolContext


class MediaArgs(Protocol):
    action: str
    output: str
    model: str | None
    thread_id: str
    prompt: str
    text: str
    voice: str
    path: str
    question: str


def _result(
    status: str, data: object = None, error: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {"schema_version": "1", "status": status, "data": data}
    if error is not None:
        value["error"] = error
    return value


def _print(value: dict[str, object], output: str) -> int:
    if output == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif value["status"] == "error":
        print(f"Erro: {value.get('error', 'falha')}", file=sys.stderr)
    else:
        print(value.get("data", ""))
    return 0 if value["status"] == "ok" else 1


def _invocation_result(value: object) -> tuple[str, object, str | None]:
    """Normalize native tool output so JSON error envelopes fail the CLI."""
    if isinstance(value, dict):
        error = value.get("error")
        return ("error", value, str(error)) if error else ("ok", value, None)
    text = str(value)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return (
            ("error", text, text) if text.startswith("Error:") else ("ok", text, None)
        )
    if isinstance(parsed, dict) and parsed.get("error"):
        return ("error", parsed, str(parsed["error"]))
    return ("ok", parsed, None)


def run_media(args: MediaArgs) -> None:
    """List or invoke a media tool with a trusted execution context."""
    if args.action == "list":
        data = [
            {"name": spec.name, "description": spec.description}
            for spec in media_specs("local")
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
        # A CLI local não recebe identidade autenticada do cliente. O launcher
        # deve fornecer uma sessão confiável para usos multiusuário; até lá,
        # operações CLI usam explicitamente o principal local.
        user_id="local",
        model=args.model or "",
        thread_id=args.thread_id,
    )
    try:
        value = asyncio.run(invoke_media(name, arguments, context))
        status, data, error = _invocation_result(value)
    except Exception as exc:
        status, data, error = "error", None, str(exc)
    raise SystemExit(_print(_result(status, data, error), args.output))

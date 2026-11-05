"""Local-trust MCP surface for the four native media tools.

The process identity is supplied by the trusted Vectora launcher through
``VECTORA_MCP_USER_ID``; tool arguments cannot override it. This stdio
transport is intentionally local-only: the parent launcher is the trust
boundary, and this module does not claim to authenticate arbitrary processes.
"""

from __future__ import annotations

import os
from uuid import uuid4

from mcp.server.fastmcp import FastMCP  # ty: ignore[unresolved-import]

from backend.services.media_adapter import invoke_media, media_specs
from backend.tools.context import ToolContext


def create_media_server() -> FastMCP:
    """Build an MCP server exposing only authorized native media tools."""
    server = FastMCP("Vectora Media")
    user_id = os.environ.get("VECTORA_MCP_USER_ID", "")
    if not user_id:
        raise RuntimeError("VECTORA_MCP_USER_ID é obrigatório para o servidor MCP")

    allowed = {spec.name for spec in media_specs(user_id)}

    def context() -> ToolContext:
        return ToolContext(user_id=user_id, thread_id="mcp", tool_call_id=uuid4().hex)

    if "generate_image" in allowed:

        @server.tool()
        async def generate_image(prompt: str) -> str:
            return await invoke_media("generate_image", {"prompt": prompt}, context())

    if "text_to_speech" in allowed:

        @server.tool()
        async def text_to_speech(text: str, voice: str = "") -> str:
            return await invoke_media(
                "text_to_speech", {"text": text, "voice": voice}, context()
            )

    if "generate_video" in allowed:

        @server.tool()
        async def generate_video(prompt: str) -> str:
            return await invoke_media("generate_video", {"prompt": prompt}, context())

    if "analyze_video" in allowed:

        @server.tool()
        async def analyze_video(path: str, question: str) -> str:
            return await invoke_media(
                "analyze_video", {"path": path, "question": question}, context()
            )

    return server


def run() -> None:
    """Run stdio for a local launcher that already authenticated the user."""
    create_media_server().run(transport="stdio")


if __name__ == "__main__":
    run()

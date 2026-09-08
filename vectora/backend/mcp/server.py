"""Authenticated MCP surface for the four native media tools.

The process identity is supplied by the trusted launcher through
``VECTORA_MCP_USER_ID``; tool arguments cannot override it.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP  # ty: ignore[unresolved-import]

from backend.services.media_adapter import invoke_media
from backend.tools.context import ToolContext


def create_media_server() -> FastMCP:
    """Build an MCP server exposing only authorized native media tools."""
    server = FastMCP("Vectora Media")
    user_id = os.environ.get("VECTORA_MCP_USER_ID", "")
    if not user_id:
        raise RuntimeError("VECTORA_MCP_USER_ID é obrigatório para o servidor MCP")

    @server.tool()
    async def generate_image(prompt: str) -> str:
        return await invoke_media(
            "generate_image",
            {"prompt": prompt},
            ToolContext(user_id=user_id, thread_id="mcp"),
        )

    @server.tool()
    async def text_to_speech(text: str, voice: str = "") -> str:
        return await invoke_media(
            "text_to_speech",
            {"text": text, "voice": voice},
            ToolContext(user_id=user_id, thread_id="mcp"),
        )

    @server.tool()
    async def generate_video(prompt: str) -> str:
        return await invoke_media(
            "generate_video",
            {"prompt": prompt},
            ToolContext(user_id=user_id, thread_id="mcp"),
        )

    @server.tool()
    async def analyze_video(path: str, question: str) -> str:
        return await invoke_media(
            "analyze_video",
            {"path": path, "question": question},
            ToolContext(user_id=user_id, thread_id="mcp"),
        )

    return server


def run() -> None:
    """Run the stdio MCP transport for a trusted local client."""
    create_media_server().run(transport="stdio")


if __name__ == "__main__":
    run()

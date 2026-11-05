"""Terminal output streaming bridge — connects the terminal tool to the UI.

Provides a lightweight callback registry so the terminal tool can emit
output lines in real-time while the async process is still running.
The chat UI registers a callback before the tool executes and unregisters
after the tool completes.

No lock needed: there is at most one active terminal process at a time
(single-threaded event loop, sequential tool execution).
"""

from collections.abc import Callable

_output_callback: Callable[[str], None] | None = None


def register_terminal_output_callback(cb: Callable[[str], None]) -> None:
    """Register a callback that receives each output line in real time.

    Args:
        cb: Callable that accepts a single ``str`` line (no trailing newline).
    """
    global _output_callback
    _output_callback = cb


def unregister_terminal_output_callback() -> None:
    """Remove the current callback (call after terminal tool finishes)."""
    global _output_callback
    _output_callback = None


def emit_terminal_line(line: str) -> None:
    """Deliver a line of terminal output to the registered callback.

    Safe to call even when no callback is registered (e.g., MCP server mode).

    Args:
        line: A single decoded output line (no trailing newline).
    """
    if _output_callback is not None:
        import contextlib

        with contextlib.suppress(Exception):
            _output_callback(line)

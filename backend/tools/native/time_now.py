"""Tool: retorna datetime atual com timezone opcional."""

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.tools.registry import ToolExtras, vtool


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="clock",
    )
)
async def time_now(timezone: str = "UTC") -> str:
    """Retorna data/hora atual no timezone especificado.

    Args:
        timezone: Timezone IANA (ex: 'UTC', 'America/Sao_Paulo')

    Returns:
        ISO datetime string ou mensagem de erro.
    """
    try:
        tz = ZoneInfo(timezone)
        return datetime.now(tz).isoformat()
    except Exception as e:
        return f"error: timezone inválido '{timezone}' — {e}"

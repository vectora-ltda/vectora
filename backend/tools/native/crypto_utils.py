"""Tools: hash, base64, jwt, regex, json."""

import base64
import hashlib
import json
import re
from typing import Any

from backend.tools.registry import ToolExtras, vtool

try:
    import jwt as _jwt

    jwt: Any = _jwt
except ImportError:
    jwt = None


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="hash",
    )
)
async def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hash de string (md5/sha256/sha512)."""
    try:
        h = hashlib.new(algorithm)
        h.update(text.encode())
        return h.hexdigest()
    except Exception as e:
        return f"error: {e}"


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="lock",
    )
)
async def base64_encode(text: str) -> str:
    """Encode base64."""
    try:
        return base64.b64encode(text.encode()).decode()
    except Exception as e:
        return f"error: {e}"


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="unlock",
    )
)
async def base64_decode(encoded: str) -> str:
    """Decode base64."""
    try:
        return base64.b64decode(encoded).decode()
    except Exception as e:
        return f"error: {e}"


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="native",
        destructive=False,
        icon="key",
    )
)
async def jwt_decode(token: str, verify: bool = False) -> str:
    """Decodifica JWT (sem verificar assinatura por padrão)."""
    if not jwt:
        return "error: PyJWT não instalado"
    try:
        decoded = jwt.decode(
            token, options={"verify_signature": verify}, algorithms=["HS256", "RS256"]
        )
        return json.dumps(decoded)
    except Exception as e:
        return f"error: {e}"


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="regex",
    )
)
async def regex_test(pattern: str, text: str) -> str:
    """Testa regex pattern em text."""
    try:
        if re.search(pattern, text):
            return "match"
        return "no match"
    except Exception as e:
        return f"error: {e}"


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="native",
        destructive=False,
        icon="braces",
    )
)
async def json_query(json_str: str, path: str) -> str:
    """Extrai valor de JSON via dot notation (a.b[0].c)."""
    try:
        obj = json.loads(json_str)
        for key in path.split("."):
            if "[" in key:
                key_name, idx = key.split("[")
                idx = int(idx.rstrip("]"))
                if key_name:
                    obj = obj[key_name]
                obj = obj[idx]
            else:
                obj = obj[key]
        return json.dumps(obj) if isinstance(obj, (dict, list)) else str(obj)
    except Exception as e:
        return f"error: {e}"

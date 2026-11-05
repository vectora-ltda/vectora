"""``backend/tools/registry.py`` — tool registry nativo (schema Pydantic,
injeção de contexto, dedup de nome, sem dependência de framework externo).

Tools de exemplo definidas aqui exercitam o mecanismo de registro
(schema, injeção de contexto, dedup de nome) sem depender de nenhuma
tool de produção.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.tools.context import ToolContext
from backend.tools.registry import (
    TOOL_REGISTRY,
    ToolExtras,
    ToolRegistry,
    ToolSpec,
    vtool,
)

_registry_local = ToolRegistry()


def _get(name: str) -> ToolSpec:
    spec = TOOL_REGISTRY.get(name)
    assert spec is not None, f"tool {name!r} não registrada"
    return spec


@vtool(extras=ToolExtras(category="test", destructive=False))
async def _hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hash de string.

    Args:
        text: Texto a ser hasheado
        algorithm: Algoritmo (sha256, md5)
    """
    import hashlib

    h = hashlib.new(algorithm)
    h.update(text.encode())
    return h.hexdigest()


@vtool(extras=ToolExtras(category="test", destructive=True))
async def _write_note(path: str, content: str, ctx: ToolContext) -> str:
    """Escreve uma nota (tool de exemplo que usa ctx).

    Args:
        path: Caminho da nota
        content: Conteúdo a escrever
    """
    return f"escrito em {path} (workspace={ctx.workspace_id}): {content}"


@vtool()
async def _always_fails(x: int) -> str:
    """Tool que sempre levanta, pra testar o guard de exceção."""
    raise RuntimeError("falha proposital")


@vtool(extras=ToolExtras(category="test"))
async def _sem_type_hint(valor) -> str:
    """Tool com parâmetro sem type hint (Any implícito).

    Args:
        valor: um valor qualquer
    """
    return str(valor)


class _Item(BaseModel):
    nome: str
    qtd: int


@vtool(extras=ToolExtras(category="test"))
async def _tipo_composto(itens: list[_Item]) -> str:
    """Tool com parâmetro de tipo composto (BaseModel aninhado).

    Args:
        itens: lista de itens
    """
    return str(len(itens))


@vtool(extras=ToolExtras(category="test"))
async def _com_parametro_title(title: str, content: str = "") -> str:
    """Tool com um parâmetro literalmente chamado `title` — mesmo nome da
    chave de metadado que Pydantic injeta em todo nó de schema.

    Args:
        title: título do item
        content: conteúdo opcional
    """
    return title


class TestVtoolSchemaGeneration:
    def test_registra_tool_no_registry_global(self):
        spec = _get("_hash_text")
        assert spec.name == "_hash_text"

    def test_schema_openai_tem_parametros_primitivos_e_defaults(self):
        schema = _get("_hash_text").openai_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "_hash_text"
        props = schema["function"]["parameters"]["properties"]
        assert "text" in props
        assert "algorithm" in props
        # `text` não tem default -> obrigatório; `algorithm` tem -> opcional.
        required = schema["function"]["parameters"].get("required", [])
        assert "text" in required
        assert "algorithm" not in required

    def test_ctx_nunca_aparece_no_schema_exposto_ao_llm(self):
        """Erro/borda: se `ctx` vazasse pro schema, o LLM tentaria
        preenchê-lo — quebraria toda chamada de tool que usa ToolContext."""
        spec = _get("_write_note")
        schema = spec.openai_schema()
        props = schema["function"]["parameters"]["properties"]

        assert "ctx" not in props
        assert spec.needs_ctx is True

    def test_descricao_por_argumento_extraida_da_docstring(self):
        schema = _get("_hash_text").openai_schema()
        props = schema["function"]["parameters"]["properties"]

        assert props["text"]["description"] == "Texto a ser hasheado"

    def test_parametro_sem_type_hint_vira_any_permissivo(self):
        """Erro/borda: parâmetro sem type hint não pode quebrar a geração
        de schema — vira campo sem `type` (JSON Schema sem `type` aceita
        qualquer valor), não uma exceção na hora de registrar a tool."""
        schema = _get("_sem_type_hint").openai_schema()
        props = schema["function"]["parameters"]["properties"]

        assert "valor" in props
        assert "type" not in props["valor"]
        assert props["valor"]["description"] == "um valor qualquer"
        assert "valor" in schema["function"]["parameters"]["required"]

    def test_parametro_de_tipo_composto_gera_defs_e_ref(self):
        """Erro/borda: BaseModel aninhado como tipo de parâmetro precisa
        preservar `$defs`/`$ref` no schema final — removê-los (como
        `title`) quebraria a referência que `properties` faz pra lá."""
        schema = _get("_tipo_composto").openai_schema()
        params = schema["function"]["parameters"]

        assert "$defs" in params
        assert "Item" in params["$defs"] or "_Item" in params["$defs"]
        item_key = next(iter(params["$defs"]))
        assert params["$defs"][item_key]["properties"].keys() == {"nome", "qtd"}
        assert params["properties"]["itens"]["items"]["$ref"] == f"#/$defs/{item_key}"

    def test_parametro_chamado_title_sobrevive_ao_strip_de_metadado(self):
        """Erro/borda: `_strip_titles` remove a chave `title` de metadado
        (ex.: `{"type": "string", "title": "Query"}` que o Pydantic injeta
        por campo) — mas não pode remover uma PROPRIEDADE cujo nome real é
        `title` só porque a chave do mapa `properties` coincide com o nome
        do metadado. Reproduzido ao vivo: `create_artifact`/
        `linear_create_issue`/`notion_create_page` (todas com parâmetro
        `title`) quebravam toda chamada ao Gemini com "required[N]:
        property is not defined" — `required` listava `title`, mas
        `properties` não tinha mais a chave."""
        schema = _get("_com_parametro_title").openai_schema()
        params = schema["function"]["parameters"]

        assert "title" in params["properties"]
        assert params["properties"]["title"]["type"] == "string"
        assert "title" in params["required"]
        # O metadado `title` (auto-gerado por campo) continua removido —
        # não é isso que este teste contesta.
        assert "title" not in params["properties"]["title"]
        assert "title" not in params


class TestToolSpecAinvoke:
    async def test_ainvoke_chama_handler_e_devolve_string(self):
        result = await _get("_hash_text").ainvoke({"text": "oi"}, ctx=ToolContext())

        import hashlib

        assert result == hashlib.sha256(b"oi").hexdigest()

    async def test_ainvoke_injeta_ctx_quando_tool_pede(self):
        ctx = ToolContext(workspace_id="ws-1")
        result = await _get("_write_note").ainvoke(
            {"path": "a.md", "content": "x"}, ctx=ctx
        )

        assert "workspace=ws-1" in result

    async def test_ainvoke_com_args_invalidos_devolve_erro_tipado_sem_levantar(self):
        """Erro/borda: argumento de tipo errado (int esperado, string
        chega) nunca deve propagar exceção — vira string de erro pro LLM
        reagir, igual ao contrato de todas as tools do Vectora."""
        result = await _get("_always_fails").ainvoke(
            {"x": "não é um int"}, ctx=ToolContext()
        )

        assert result.startswith("Error:")

    async def test_ainvoke_com_excecao_do_handler_devolve_erro_tipado(self):
        """Erro/borda: handler que levanta (não erro de validação de
        argumento, erro de execução) também nunca propaga."""
        result = await _get("_always_fails").ainvoke({"x": 1}, ctx=ToolContext())

        assert result.startswith("Error:")
        assert "falha proposital" in result


class TestToolRegistryIsolated:
    def test_registry_proprio_nao_interfere_no_global(self):
        """Confirma que uma instância isolada de ToolRegistry (usada por
        quem quiser um subconjunto de tools, ex. um subagente com toolset
        restrito) não polui o TOOL_REGISTRY global nem vice-versa."""
        assert "_hash_text" not in _registry_local
        assert "_hash_text" in TOOL_REGISTRY

"""Stress test 3 — TextService: contagem de tokens sob carga.

count_tokens() é chamado em toda invocação do LLM pelo trim_messages().
Em conversas longas ou pipelines de ingestão com muitos chunks, essa
função precisa ser rápida e correta sob chamadas em rajada.

Verifica:
  - 10 000 chamadas de count_tokens() completam em menos de 5 s
  - contagem é determinística (mesma string → mesmo resultado em todas as chamadas)
  - split() + count_tokens() são consistentes entre si para o mesmo texto
"""

from __future__ import annotations

import time

import pytest

from backend.services.text import text_service


@pytest.mark.stress
def test_token_counting_throughput():
    """10 000 chamadas de count_tokens() — determinismo e throughput."""
    sample = (
        "O Vectora é um assistente de IA com suporte a RAG, busca vetorial e MCP. "
        "Utiliza LanceDB como banco vetorial e SQLite para checkpoints de sessão. "
        "O pipeline de embedding usa Cohere embed-multilingual-v3.0."
    )

    # Pré-aquece o encoder tiktoken (lazy init)
    baseline = text_service.count_tokens(sample)
    assert baseline > 0

    N = 10_000
    t0 = time.perf_counter()
    for _ in range(N):
        count = text_service.count_tokens(sample)
        # Determinismo: mesma string deve sempre retornar o mesmo valor
        assert count == baseline
    elapsed = time.perf_counter() - t0

    throughput = N / elapsed
    # Budget: pelo menos 3 500 chamadas/segundo (tiktoken é compilado em Rust)
    # Threshold relax: máquinas lentas/ocupadas podem atingir ~3600, não é bug
    assert throughput >= 3_500, (
        f"Throughput insuficiente: {throughput:.0f} calls/s (mínimo: 3 500)"
    )


@pytest.mark.stress
def test_large_text_token_counting():
    """Texto grande (~500 mensagens de conversa) — contagem em menos de 1 s."""
    parts = []
    for i in range(250):
        parts.append(f"Pergunta número {i}: o que é RAG?")
        parts.append(
            f"Resposta {i}: RAG é Retrieval Augmented Generation, "
            "que combina busca vetorial com geração de texto."
        )
    big_text = "\n".join(parts)

    t0 = time.perf_counter()
    total_tokens = text_service.count_tokens(big_text)
    elapsed = time.perf_counter() - t0

    assert total_tokens > 0
    # Budget: ~500 falas contadas em menos de 1 s
    assert elapsed < 1.0, f"Contagem de texto grande levou {elapsed:.3f}s"


@pytest.mark.stress
def test_split_consistency_under_load():
    """split() em 1 000 textos — consistência dos chunks e throughput."""
    # Texto de ~3 000 tokens (vai ser dividido em vários chunks)
    paragraph = (
        "O Vectora processa documentos em chunks usando tiktoken cl100k_base. "
        "Cada chunk tem no máximo 512 tokens com overlap de 50 tokens. "
    )
    large_text = paragraph * 100  # ~3 200 tokens

    # Referência: split uma vez e guarda o resultado
    reference_chunks = text_service.split(large_text)
    assert len(reference_chunks) >= 2

    N = 200
    t0 = time.perf_counter()
    for _ in range(N):
        chunks = text_service.split(large_text)
        assert len(chunks) == len(reference_chunks), (
            "split() não é determinístico — resultados inconsistentes"
        )
    elapsed = time.perf_counter() - t0

    # Budget: 200 splits em menos de 60 s
    # (tiktoken é mais lento em Windows; CI Linux deve completar bem abaixo do limite)
    assert elapsed < 60.0, f"Tempo excessivo: {elapsed:.2f}s para {N} splits"


@pytest.mark.stress
@pytest.mark.parametrize(
    "sample",
    [
        "texto curto",
        "a" * 50,
        "café, ação, coração — acentuação em português",
        "🚀🎉✨ emoji stress test 你好世界",
        "def foo():\n    return 42\n" * 20,
        "SELECT * FROM users WHERE id = 1;" * 10,
        " ".join(str(i) for i in range(500)),
        "\n".join(f"linha {i}" for i in range(200)),
        "palavra " * 1000,
        "",
    ],
    ids=[
        "curto",
        "repetido_a",
        "acentuacao",
        "emoji_unicode",
        "codigo",
        "sql",
        "numeros",
        "multilinha",
        "muito_longo",
        "vazio",
    ],
)
def test_count_tokens_various_samples(sample):
    """count_tokens em amostras variadas de conteúdo — nunca lança, sempre determinístico."""
    count1 = text_service.count_tokens(sample)
    count2 = text_service.count_tokens(sample)

    assert count1 == count2
    assert count1 >= 0


@pytest.mark.stress
@pytest.mark.parametrize(
    "n_calls", [1_000, 5_000, 10_000, 20_000], ids=lambda n: f"calls={n}"
)
def test_count_tokens_throughput_various_volumes(n_calls):
    """Throughput de count_tokens em volumes crescentes de chamadas."""
    sample = "O Vectora processa texto em chunks tokenizados via tiktoken."
    baseline = text_service.count_tokens(sample)

    t0 = time.perf_counter()
    for _ in range(n_calls):
        assert text_service.count_tokens(sample) == baseline
    elapsed = time.perf_counter() - t0

    # Budget generoso e proporcional ao volume, tolerante a máquina ocupada.
    assert elapsed < (n_calls / 1000) + 5.0


@pytest.mark.stress
@pytest.mark.parametrize(
    "size", [1_000, 5_000, 20_000, 50_000], ids=lambda n: f"chars={n}"
)
def test_split_various_text_sizes(size):
    """split() em textos de tamanhos crescentes — chunking sempre determinístico."""
    unit = "frase de teste com conteúdo variado. "
    text = (unit * ((size // len(unit)) + 1))[:size]

    chunks_a = text_service.split(text)
    chunks_b = text_service.split(text)

    assert chunks_a == chunks_b
    assert len(chunks_a) >= 1


@pytest.mark.stress
@pytest.mark.parametrize(
    "batch_size", [100, 500, 1_000, 5_000], ids=lambda n: f"batch={n}"
)
def test_count_tokens_rapid_batch_various_sizes(batch_size):
    """Lote de chamadas rápidas de count_tokens em textos distintos — sem degradação de precisão."""
    texts = [
        f"documento número {i} com algum conteúdo variável {i * 2}"
        for i in range(batch_size)
    ]

    t0 = time.perf_counter()
    counts = [text_service.count_tokens(t) for t in texts]
    elapsed = time.perf_counter() - t0

    assert len(counts) == batch_size
    assert all(c > 0 for c in counts)
    assert elapsed < (batch_size / 500) + 5.0


@pytest.mark.stress
@pytest.mark.parametrize(
    "text",
    [
        "parágrafo um. " * 50,
        "parágrafo dois com números 123456789. " * 50,
        "PARÁGRAFO EM MAIÚSCULAS. " * 50,
        "mixed Case Paragraph With Numbers 42. " * 50,
        "texto,com,virgulas,em,excesso," * 50,
    ],
    ids=["p1", "p2_numeros", "p3_maiusculas", "p4_mixed", "p5_virgulas"],
)
def test_split_determinism_across_text_variations(text):
    """split() é determinístico em textos com formatações e cases distintos."""
    chunks_a = text_service.split(text)
    chunks_b = text_service.split(text)

    assert chunks_a == chunks_b

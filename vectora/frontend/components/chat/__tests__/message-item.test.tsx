// @vitest-environment jsdom
/**
 * Tests para MessageItem: render de mensagem do usuário e do assistente
 * (com strip do envelope markdown), e botão de copiar.
 */

import { describe, expect, it, afterEach, vi } from "vitest";
import {
  render as rtlRender,
  screen,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MessageItem } from "../message-item";
import type { Message } from "@/lib/types";

afterEach(cleanup);

// MessageItem usa useQueryClient (invalida o histórico após "Retroceder"),
// então precisa de um provider mesmo nos testes que não exercitam rewind.
// Passado como `wrapper` (não inline no elemento) porque `rerender` do RTL
// substitui a árvore inteira e perderia um provider inline.
function render(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return rtlRender(ui, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  });
}

type Props = Parameters<typeof MessageItem>[0];

function msg(over: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    timestamp: new Date(),
    ...over,
  } as unknown as Message;
}

function baseProps(message: Message, over: Partial<Props> = {}): Props {
  return {
    message,
    isLastAssistant: false,
    isRegenerating: false,
    copiedId: null,
    onCopy: vi.fn(),
    onRegenerate: vi.fn(),
    feedbackComment: {},
    showCommentInput: null,
    onFeedback: vi.fn(),
    onSubmitComment: vi.fn(),
    onCancelComment: vi.fn(),
    onToggleComment: vi.fn(),
    setFeedbackComment: vi.fn(),
    ...over,
  } as Props;
}

describe("MessageItem", () => {
  it("renderiza o conteúdo de uma mensagem do usuário", () => {
    render(
      <MessageItem
        {...baseProps(msg({ role: "user", content: "olá mundo" }))}
      />,
    );
    expect(screen.getByText("olá mundo")).toBeInTheDocument();
  });

  it("remove o envelope markdown do conteúdo do assistente", () => {
    render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "``````markdown\nResposta limpa\n``````",
          }),
        )}
      />,
    );
    expect(screen.getByText("Resposta limpa")).toBeInTheDocument();
    expect(screen.queryByText(/``````/)).toBeNull();
  });

  it("copiar chama onCopy com o conteúdo e o id", () => {
    const onCopy = vi.fn();
    render(
      <MessageItem
        {...baseProps(msg({ id: "x9", role: "assistant", content: "texto" }), {
          onCopy,
        })}
      />,
    );
    const copyBtn = screen
      .getAllByRole("button")
      .find((b) => /copiar|copy/i.test(b.getAttribute("aria-label") ?? ""));
    expect(copyBtn).toBeTruthy();
    fireEvent.click(copyBtn!);
    expect(onCopy).toHaveBeenCalledWith("texto", "x9");
  });

  it("mensagem do usuário tem botão copiar", () => {
    const onCopy = vi.fn();
    render(
      <MessageItem
        {...baseProps(
          msg({ id: "u1", role: "user", content: "minha pergunta" }),
          {
            onCopy,
          },
        )}
      />,
    );
    const copyBtn = screen
      .getAllByRole("button")
      .find((b) => /copiar|copy/i.test(b.getAttribute("aria-label") ?? ""));
    expect(copyBtn).toBeTruthy();
    fireEvent.click(copyBtn!);
    expect(onCopy).toHaveBeenCalledWith("minha pergunta", "u1");
  });

  it("botões do assistente usam h-6 w-6 (não h-7 w-7)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({ id: "a2", role: "assistant", content: "resposta" }),
        )}
      />,
    );
    const buttons = container.querySelectorAll("button");
    const actionBtns = Array.from(buttons).filter(
      (b) => b.className.includes("h-6") && b.className.includes("w-6"),
    );
    expect(actionBtns.length).toBeGreaterThan(0);
    // Nenhum botão de ação deve usar h-7 w-7
    const oldSizeBtns = Array.from(buttons).filter(
      (b) => b.className.includes("h-7") && b.className.includes("w-7"),
    );
    expect(oldSizeBtns).toHaveLength(0);
  });

  // ── Layout WhatsApp ────────────────────────────────────────────────────────

  it("mensagem do usuário tem container com justify-end (alinhamento direita)", () => {
    const { container } = render(
      <MessageItem {...baseProps(msg({ role: "user", content: "olá" }))} />,
    );
    const outer = container.querySelector(".justify-end");
    expect(outer).toBeTruthy();
  });

  it("mensagem do assistente NÃO tem justify-end", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(msg({ role: "assistant", content: "resposta" }))}
      />,
    );
    expect(container.querySelector(".justify-end")).toBeNull();
  });

  it("bolha do usuário usa bg-muted e não bg-user-bubble", () => {
    const { container } = render(
      <MessageItem {...baseProps(msg({ role: "user", content: "olá" }))} />,
    );
    const bubble = container.querySelector(".bg-muted");
    expect(bubble).toBeTruthy();
    expect(container.querySelector(".bg-user-bubble")).toBeNull();
  });

  it("bolha do assistente não tem fundo (nem bg-muted nem bg-user-bubble)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(msg({ role: "assistant", content: "resposta" }))}
      />,
    );
    expect(container.querySelector(".bg-muted")).toBeNull();
    expect(container.querySelector(".bg-user-bubble")).toBeNull();
  });

  it("nenhum avatar de assistente é renderizado (visual limpo, ícone removido)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(msg({ role: "assistant", content: "resposta" }))}
      />,
    );
    const assistantImg = Array.from(container.querySelectorAll("img")).find(
      (i) => /assistant/i.test(i.getAttribute("alt") ?? ""),
    );
    expect(assistantImg).toBeUndefined();
  });

  it("também ausente para mensagem do usuário (edge, mesma checagem)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(msg({ role: "user", content: "pergunta" }))}
      />,
    );
    const assistantImg = Array.from(container.querySelectorAll("img")).find(
      (i) => /assistant/i.test(i.getAttribute("alt") ?? ""),
    );
    expect(assistantImg).toBeUndefined();
  });

  it("bolha do usuário tem max-w-[85%] para limitar largura", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({ role: "user", content: "mensagem longa ".repeat(20) }),
        )}
      />,
    );
    // O wrapper interno da bolha do usuário deve ter max-w-[85%]
    const wrapper = container.querySelector(".max-w-\\[85\\%\\]");
    expect(wrapper).toBeTruthy();
  });

  it("bolha do assistente usa flex-1 (não max-w-[85%])", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(msg({ role: "assistant", content: "resposta" }))}
      />,
    );
    expect(container.querySelector(".flex-1")).toBeTruthy();
    expect(container.querySelector(".max-w-\\[85\\%\\]")).toBeNull();
  });

  // ── Bloco de progresso do agente ──────────────────────────────────────────

  it("bloco <details> aparece quando isThinking=true (streaming ativo)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "",
            isThinking: true,
            thinkingStartTime: Date.now(),
          }),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeTruthy();
  });

  it("bloco <details> aparece quando há thinkingSteps (pós-stream)", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "resposta",
            isThinking: false,
            thinkingSteps: ["Passo 1", "Passo 2"],
          }),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeTruthy();
    // Os passos devem aparecer no conteúdo
    expect(screen.getByText("Passo 1")).toBeInTheDocument();
    expect(screen.getByText("Passo 2")).toBeInTheDocument();
  });

  it("bloco <details> NÃO aparece com thinkingStartTime setado mas isThinking=false e sem steps", () => {
    // thinkingStartTime sozinho não deve bastar pra exibir o bloco — só
    // isThinking=true ou thinkingSteps não-vazio o fazem.
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "resposta",
            isThinking: false,
            thinkingStartTime: Date.now() - 5000,
            thinkingSteps: [],
          }),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("bloco <details> NÃO aparece quando não há dados de thinking", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "resposta simples",
            isThinking: false,
          }),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("bloco <details> NÃO aparece para mensagem do usuário mesmo com thinkingSteps", () => {
    // Guard: usuário nunca exibe bloco de thinking
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "user",
            content: "pergunta",
            isThinking: true,
            thinkingSteps: ["Passo X"],
          } as unknown as Message),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("thinkingSteps vazio (length=0) não exibe bloco <details>", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: "resposta",
            isThinking: false,
            thinkingSteps: [],
          }),
        )}
      />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  // ── Casos limite de conteúdo ───────────────────────────────────────────────

  it("conteúdo vazio não quebra a renderização", () => {
    expect(() =>
      render(
        <MessageItem {...baseProps(msg({ role: "assistant", content: "" }))} />,
      ),
    ).not.toThrow();
  });

  it("conteúdo muito longo renderiza sem erro", () => {
    const longContent = "palavra ".repeat(500);
    expect(() =>
      render(
        <MessageItem
          {...baseProps(msg({ role: "user", content: longContent }))}
        />,
      ),
    ).not.toThrow();
    expect(screen.getByText(longContent.trim())).toBeInTheDocument();
  });

  it("remove o envelope mesmo com conteúdo markdown extenso", () => {
    const body =
      "# Título\n\n- item 1\n- item 2\n\n```python\nprint('hi')\n```";
    render(
      <MessageItem
        {...baseProps(
          msg({
            role: "assistant",
            content: `\`\`\`\`\`\`markdown\n${body}\n\`\`\`\`\`\``,
          }),
        )}
      />,
    );
    expect(screen.getByText("Título")).toBeInTheDocument();
    expect(screen.queryByText(/``````/)).toBeNull();
  });

  // Metadata (timestamp) fica na barra de botões, não na bolha de conteúdo
  it("metadata de duração está na barra justify-between, não na bolha de markdown", () => {
    const { container } = render(
      <MessageItem
        {...baseProps(
          msg({
            id: "a3",
            role: "assistant",
            content: "texto da resposta",
            isThinking: false,
            thinkingDuration: 3000,
          }),
        )}
      />,
    );
    expect(screen.getByText("texto da resposta")).toBeInTheDocument();
    // Barra de botões usa justify-between para posicionar botões + metadata
    const metaBar = container.querySelector("[class*='justify-between']");
    expect(metaBar).toBeTruthy();
    // Span de metadata está dentro da barra (classe tabular-nums)
    const metaSpan = metaBar?.querySelector("[class*='tabular-nums']");
    expect(metaSpan).toBeTruthy();
    expect(metaSpan?.textContent).toMatch(/3\.0s/);
  });

  it("clicar na thumbnail de imagem abre o lightbox em tela cheia", () => {
    render(
      <MessageItem
        {...baseProps(
          msg({
            role: "user",
            content: "olha essa imagem",
            images: [
              {
                id: "img1",
                name: "foto.png",
                mimeType: "image/png",
                base64: "AAAA",
                size: 1234,
              },
            ],
          }),
        )}
      />,
    );

    fireEvent.click(screen.getByAltText("foto.png"));

    // Lightbox mostra a mesma imagem em um <img> adicional (dialog)
    expect(screen.getAllByAltText("foto.png")).toHaveLength(2);
  });

  it("fechar o lightbox some com a segunda instância da imagem (edge)", () => {
    render(
      <MessageItem
        {...baseProps(
          msg({
            role: "user",
            content: "olha essa imagem",
            images: [
              {
                id: "img1",
                name: "foto.png",
                mimeType: "image/png",
                base64: "AAAA",
                size: 1234,
              },
            ],
          }),
        )}
      />,
    );

    fireEvent.click(screen.getByAltText("foto.png"));
    expect(screen.getAllByAltText("foto.png")).toHaveLength(2);

    fireEvent.click(screen.getByTitle("Cancel"));
    expect(screen.getAllByAltText("foto.png")).toHaveLength(1);
  });
});

// ============================================================================
// Streaming incremental — acumulação sem duplicação de tokens
//
// Via re-render sucessivo (o mesmo mecanismo que use-stream-handler usa:
// content cresce a cada setMessages), verifica que cada token do astream
// acumula numa única string contínua, sem cada um virar seu próprio
// parágrafo nem duplicar. MessageItem é o componente de apresentação — não
// é responsável por deduplicar (isso é do hook/adapter, coberto em
// use-stream-handler.test.ts e test_adapters_streaming.py), mas precisa
// renderizar fielmente qualquer sequência de conteúdo crescente sem
// introduzir quebras de parágrafo ou duplicação por conta própria.
// ============================================================================

function expectNoDuplication(history: string[], full: string) {
  // Cada estado intermediário é um PREFIXO do texto final acumulado —
  // nunca maior (duplicado) nem contendo o mesmo trecho duas vezes.
  for (const snapshot of history) {
    expect(full.startsWith(snapshot) || snapshot === full).toBe(true);
  }
  // O último snapshot bate exatamente com o texto completo esperado.
  expect(history.at(-1)).toBe(full);
}

/** Simula o acúmulo incremental de tokens e devolve o histórico renderizado. */
function streamContent(chunks: string[]): {
  getText: () => string;
  history: string[];
  unmount: () => void;
} {
  let acc = "";
  const history: string[] = [];
  const { rerender, unmount } = render(
    <MessageItem
      {...baseProps(msg({ id: "stream-1", role: "assistant", content: "" }))}
    />,
  );
  for (const chunk of chunks) {
    acc += chunk;
    rerender(
      <TooltipProvider>
        <MessageItem
          {...baseProps(
            msg({ id: "stream-1", role: "assistant", content: acc }),
          )}
        />
      </TooltipProvider>,
    );
    history.push(
      screen.getByTestId("message-content-assistant").textContent ?? "",
    );
  }
  return {
    getText: () =>
      screen.getByTestId("message-content-assistant").textContent ?? "",
    history,
    unmount,
  };
}

describe("MessageItem — streaming incremental (token a token, como astream)", () => {
  it("1. streaming char-a-char do texto exato do bug reportado", () => {
    const full = "Olá! Como posso ajudar você hoje?";
    const { getText, history } = streamContent(full.split(""));
    expect(getText()).toBe(full);
    expectNoDuplication(history, full);
  });

  it("2. streaming palavra-a-palavra (frase crescente)", () => {
    const words = ["Olá!", " Como", " posso", " ajudar", " você", " hoje?"];
    const full = words.join("");
    const { getText, history } = streamContent(words);
    expect(getText()).toBe(full);
    expectNoDuplication(history, full);
  });

  it("3. nenhum estado intermediário duplica o trecho já renderizado", () => {
    const chunks = ["A", "B", "C", "D", "E"];
    const { history } = streamContent(chunks);
    expect(history).toEqual(["A", "AB", "ABC", "ABCD", "ABCDE"]);
  });

  it("4. envelope markdown de 6 crases fechado no final não aparece no texto", () => {
    const chunks = ["``````markdown\n", "Conteúdo limpo", "\n``````"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("Conteúdo limpo");
    expect(getText()).not.toContain("`");
  });

  it("5. envelope markdown de 3 crases fechado no final não aparece no texto", () => {
    const chunks = ["```markdown\n", "Resposta", "\n```"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("Resposta");
  });

  it("6. envelope apenas aberto (streaming ainda em andamento) mostra parcial sem o fence", () => {
    const chunks = ["```markdown\n", "ainda gerando"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("ainda gerando");
    expect(getText()).not.toContain("```");
  });

  it("7. reset simulando message_break não duplica o segmento anterior", () => {
    // Simula: token acumula com fence -> message_break aplica strip
    // (substitui o acumulado pela versão sem fence) -> mais tokens chegam.
    const { getText, history, unmount } = streamContent([
      "```markdown\nSeg",
      "mento 1\n```",
    ]);
    expect(getText()).toBe("Segmento 1");
    // Reseta para a versão stripped (o que message_break faz no hook real)
    // e continua acumulando — não deve haver o fence nem duplicação.
    const stripped = getText();
    unmount();
    const { getText: getText2 } = streamContent([stripped, "\n\nSegmento 2"]);
    // "\n\n" vira quebra de parágrafo (dois <p>) — textContent concatena
    // sem duplo separador; o importante é que "Segmento 1" aparece uma
    // única vez, não duas (a regressão original duplicava o segmento).
    expect(getText2()).toBe("Segmento 1\nSegmento 2");
    expect(getText2().match(/Segmento 1/g)?.length).toBe(1);
    expect(history.length).toBeGreaterThan(0);
  });

  it("8. emoji multi-byte streamado em pedaços completos", () => {
    const chunks = ["Vamos ", "🎉", " comemorar ", "🚀", "!"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("Vamos 🎉 comemorar 🚀!");
  });

  it("9. acentuação streamada caractere por caractere", () => {
    const full = "função, coração, não, ação";
    const { getText } = streamContent(full.split(""));
    expect(getText()).toBe(full);
  });

  it("10. resposta longa (200 chunks de 1 char) — comprimento final exato", () => {
    const full = Array.from({ length: 200 }, (_, i) => String(i % 10)).join("");
    const { getText } = streamContent(full.split(""));
    expect(getText()).toBe(full);
    expect(getText().length).toBe(200);
  });

  it("11. pontuação isolada como token único entre palavras", () => {
    const chunks = ["Sim", ",", " com certeza", "!", " Combinado", "."];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("Sim, com certeza! Combinado.");
  });

  it("12. espaços e quebras de linha como tokens isolados", () => {
    // "\n\n" vira quebra de parágrafo (dois <p> no markdown renderizado) —
    // textContent concatena sem inserir separador extra entre os blocos.
    const chunks = ["Linha1", "\n", "\n", "Linha2", " ", " ", "fim"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("Linha1\nLinha2  fim");
    expect(getText()).toContain("Linha1");
    expect(getText()).toContain("Linha2  fim");
  });

  it("13. conteúdo que encolhe entre renders (regenerate) não quebra e mostra o estado final", () => {
    const { rerender } = render(
      <MessageItem
        {...baseProps(
          msg({
            id: "shrink-1",
            role: "assistant",
            content: "resposta longa demais",
          }),
        )}
      />,
    );
    rerender(
      <TooltipProvider>
        <MessageItem
          {...baseProps(
            msg({ id: "shrink-1", role: "assistant", content: "curta" }),
          )}
        />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("message-content-assistant").textContent).toBe(
      "curta",
    );
  });

  it("14. salto direto de vazio para texto completo (sem streaming incremental)", () => {
    const full = "Resposta completa numa única atualização.";
    const { getText, history } = streamContent([full]);
    expect(getText()).toBe(full);
    expect(history).toEqual([full]);
  });

  it("15. URL streamada em pedaços aparece uma única vez no texto final", () => {
    const chunks = ["Veja: ", "https://", "docs.vectora", ".company/guia"];
    const { getText } = streamContent(chunks);
    const full = chunks.join("");
    expect(getText()).toBe(full);
    expect(getText().match(/https:\/\//g)?.length).toBe(1);
  });

  it("16. bloco de código (não-envelope) com fence aberto sem fechar ainda", () => {
    const chunks = ["Exemplo:\n\n```python\n", "def foo():\n    pass"];
    const { getText } = streamContent(chunks);
    // Não é o envelope reservado ("markdown") — não deve ser stripado; o
    // fence vira bloco de código (via SyntaxHighlighter), então "```python"
    // literal não aparece no texto visível, mas o código em si permanece.
    expect(getText()).toContain("def foo():");
    expect(getText()).not.toContain("```");
  });

  it("17. lista numerada streamada linha por linha", () => {
    const chunks = ["1. Primeiro\n", "2. Segundo\n", "3. Terceiro"];
    const { getText } = streamContent(chunks);
    expect(getText()).toContain("Primeiro");
    expect(getText()).toContain("Segundo");
    expect(getText()).toContain("Terceiro");
  });

  it("18. isThinking com content vazio renderiza cursor, não texto duplicado", () => {
    render(
      <MessageItem
        {...baseProps(
          msg({
            id: "thinking-1",
            role: "assistant",
            content: "",
            isThinking: true,
          }),
        )}
      />,
    );
    const el = screen.getByTestId("message-content-assistant");
    expect(el.textContent).toBe("");
    expect(el.getAttribute("data-streaming")).toBe("true");
  });

  it("19. chunks duplicados no input renderizam exatamente o que foi passado (isola responsabilidade)", () => {
    // Se o upstream (hook/adapter) reintroduzisse duplicação, MessageItem
    // não deve amplificá-la nem corrigi-la silenciosamente — só renderiza
    // fielmente o `content` recebido. A deduplicação é responsabilidade do
    // hook (use-stream-handler.test.ts) e do adapter
    // (test_adapters_streaming.py), não deste componente de apresentação.
    const chunks = ["Olá", "Olá", "!", "!"];
    const { getText } = streamContent(chunks);
    expect(getText()).toBe("OláOlá!!");
  });

  it("20. resultado final idêntico streamando char-a-char vs de uma vez só", () => {
    const full = "Comparação de snapshots finais idênticos.";
    const streamed = streamContent(full.split(""));
    streamed.unmount();
    const atOnce = streamContent([full]);
    expect(atOnce.getText()).toBe(streamed.history.at(-1));
    expect(atOnce.getText()).toBe(full);
  });

  it("mock variado: onCopy não é chamado apenas por re-renderizar durante o streaming", () => {
    const onCopy = vi.fn();
    let acc = "";
    const { rerender } = render(
      <MessageItem
        {...baseProps(msg({ id: "nocall-1", role: "assistant", content: "" }), {
          onCopy,
        })}
      />,
    );
    for (const chunk of ["a", "b", "c"]) {
      acc += chunk;
      rerender(
        <TooltipProvider>
          <MessageItem
            {...baseProps(
              msg({ id: "nocall-1", role: "assistant", content: acc }),
              { onCopy },
            )}
          />
        </TooltipProvider>,
      );
    }
    expect(onCopy).not.toHaveBeenCalled();
  });

  it("mock variado: isRegenerating=true durante streaming não altera o texto acumulado", () => {
    let acc = "";
    const { rerender } = render(
      <MessageItem
        {...baseProps(msg({ id: "regen-1", role: "assistant", content: "" }), {
          isRegenerating: true,
        })}
      />,
    );
    for (const chunk of ["X", "Y", "Z"]) {
      acc += chunk;
      rerender(
        <TooltipProvider>
          <MessageItem
            {...baseProps(
              msg({ id: "regen-1", role: "assistant", content: acc }),
              { isRegenerating: true },
            )}
          />
        </TooltipProvider>,
      );
    }
    expect(screen.getByTestId("message-content-assistant").textContent).toBe(
      "XYZ",
    );
  });
});

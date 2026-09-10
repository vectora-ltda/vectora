// @vitest-environment jsdom
/**
 * MessageList — posicionamento de scroll ao abrir/trocar de thread.
 *
 * O conteúdo fica visibility:hidden enquanto a altura ainda cresce
 * (markdown/syntax highlight sendo medidos) e é revelado quando estabiliza.
 * O reposicionamento é um SALTO direto (scrollTop = scrollHeight), disparado
 * por ResizeObserver — nunca rolagem incremental de velocidade constante, que
 * fazia conversas longas demorarem proporcionalmente ao tamanho.
 *
 * A posição de scroll é preservada por thread entre montagens, porque trocar
 * de modo (Assistente/IDE/Kanban) remonta o chat.
 */

import { describe, expect, it, afterEach, beforeAll, vi } from "vitest";
import {
  act,
  render,
  screen,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { MessageList } from "../message-list";
import type { Message } from "@/lib/types";
vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    message_list_aria: () => "Messages",
    scroll_back_to_bottom: () => "Back to bottom",
  },
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    message_list_aria: () => "Messages",
    scroll_back_to_bottom: () => "Back to bottom",
  },
}));

vi.mock("../message-item", () => ({
  MessageItem: ({ message }: { message: Message }) => (
    <div data-testid="message-item">{message.content}</div>
  ),
}));

vi.mock("../message-skeleton", () => ({
  MessageSkeletons: () => <div data-testid="message-skeletons" />,
}));

const { scrollToIndexMock } = vi.hoisted(() => ({
  scrollToIndexMock: vi.fn(),
}));

// Thread virtualizada (> VIRTUALIZE_THRESHOLD mensagens): escrever
// scrollTop direto no container não é confiável com @tanstack/react-virtual
// (ele mantém seu próprio offset interno) — precisa passar por scrollToIndex.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getVirtualItems: () => [],
    getTotalSize: () => 20000,
    scrollToIndex: scrollToIndexMock,
    measureElement: () => {},
  }),
}));

// jsdom não implementa Element.scrollTo nem ResizeObserver.
beforeAll(() => {
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = () => {};
  }
  if (!("ResizeObserver" in globalThis)) {
    class FakeResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver =
      FakeResizeObserver;
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  scrollToIndexMock.mockClear();
});

function msg(id: string, content: string): Message {
  return {
    id,
    role: "assistant",
    content,
    timestamp: new Date(),
  } as unknown as Message;
}

function baseProps(messages: Message[]) {
  return {
    messages,
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
  };
}

/** Altura fixa e mensurável, com scrollTop realmente gravável (jsdom não
 * implementa layout, então scrollTop é só uma propriedade). */
function mockScrollMetrics(container: HTMLElement, scrollHeight = 2000) {
  Object.defineProperty(container, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(container, "clientHeight", {
    configurable: true,
    get: () => 500,
  });
}

describe("MessageList — posicionamento de scroll ao abrir a thread", () => {
  it("esconde o conteúdo durante a convergência e revela ao estabilizar, posicionado no fim", async () => {
    vi.useFakeTimers();
    render(<MessageList {...baseProps([msg("m1", "olá")])} threadId="t1" />);

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);

    const content = screen.getByTestId("message-list-content");
    expect(content).toHaveStyle({ visibility: "hidden" });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(content).toHaveStyle({ visibility: "visible" });
    // Salto direto pro fim — não uma posição intermediária.
    expect(container.scrollTop).toBe(2000);
  });

  it("erro/borda: a janela de convergência sempre termina, mesmo sem o ResizeObserver disparar", async () => {
    vi.useFakeTimers();
    render(<MessageList {...baseProps([msg("m1", "olá")])} threadId="t-cap" />);

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);
    const content = screen.getByTestId("message-list-content");
    expect(content).toHaveStyle({ visibility: "hidden" });

    // Nenhum callback de ResizeObserver é emitido (o fake não dispara) —
    // mesmo assim o conteúdo precisa ser revelado pelo teto da janela.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });

    expect(content).toHaveStyle({ visibility: "visible" });
  });

  it("não reesconde o conteúdo quando a thread não muda (nova mensagem via streaming)", async () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <MessageList {...baseProps([msg("m1", "olá")])} threadId="t2" />,
    );

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    const content = screen.getByTestId("message-list-content");
    expect(content).toHaveStyle({ visibility: "visible" });

    rerender(
      <MessageList
        {...baseProps([msg("m1", "olá"), msg("m2", "segunda mensagem")])}
        threadId="t2"
      />,
    );

    expect(content).toHaveStyle({ visibility: "visible" });
  });
});

describe("MessageList — scroll sobrevive à remontagem (troca de modo)", () => {
  it("restaura a posição salva da thread em vez de voltar ao topo", async () => {
    vi.useFakeTimers();
    const messages = [msg("m1", "olá"), msg("m2", "tchau")];
    const { unmount } = render(
      <MessageList {...baseProps(messages)} threadId="t-modo" />,
    );

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Usuário rola pra uma posição intermediária e troca de modo.
    container.scrollTop = 850;
    unmount();

    render(<MessageList {...baseProps(messages)} threadId="t-modo" />);
    const restored = screen.getByLabelText("Messages");
    mockScrollMetrics(restored);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(restored.scrollTop).toBe(850);
  });

  it("erro/borda: thread sem posição salva abre no fim, não em 0", async () => {
    vi.useFakeTimers();
    render(
      <MessageList {...baseProps([msg("m1", "olá")])} threadId="t-nova" />,
    );

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container, 4000);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(container.scrollTop).toBe(4000);
  });
});

describe("MessageList — botão 'Voltar ao fim'", () => {
  it("aparece ao rolar pra cima e some ao voltar ao fim", async () => {
    vi.useFakeTimers();
    render(<MessageList {...baseProps([msg("m1", "olá")])} threadId="t-btn" />);

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Nenhum botão enquanto o scroll está no fim.
    expect(screen.queryByLabelText("Back to bottom")).not.toBeInTheDocument();

    // Usuário rola pra cima.
    container.scrollTop = 200;
    await act(async () => {
      fireEvent.scroll(container);
    });
    expect(screen.getByLabelText("Back to bottom")).toBeInTheDocument();

    // E volta ao fim: 2000 - 500 = 1500 é o scrollTop máximo aqui.
    container.scrollTop = 1500;
    await act(async () => {
      fireEvent.scroll(container);
    });
    expect(screen.queryByLabelText("Back to bottom")).not.toBeInTheDocument();
  });

  it("clicar no botão leva ao fim de uma vez, sem posição intermediária", async () => {
    vi.useFakeTimers();
    render(
      <MessageList {...baseProps([msg("m1", "olá")])} threadId="t-btn2" />,
    );

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    container.scrollTop = 100;
    await act(async () => {
      fireEvent.scroll(container);
    });

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Back to bottom"));
    });

    expect(container.scrollTop).toBe(2000);
    expect(screen.queryByLabelText("Back to bottom")).not.toBeInTheDocument();
  });
});

describe("MessageList — skeletons de carregamento", () => {
  it("mostra skeletons e esconde a lista enquanto o histórico carrega", () => {
    render(
      <MessageList
        {...baseProps([msg("m1", "olá")])}
        threadId="t-load"
        isLoadingThread
      />,
    );

    expect(screen.getByTestId("message-skeletons")).toBeInTheDocument();
    expect(
      screen.queryByTestId("message-list-content"),
    ).not.toBeInTheDocument();
  });

  it("erro/borda: o botão 'Voltar ao fim' nunca aparece durante o carregamento", async () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <MessageList {...baseProps([msg("m1", "olá")])} threadId="t-load2" />,
    );
    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    container.scrollTop = 100;
    await act(async () => {
      fireEvent.scroll(container);
    });
    expect(screen.getByLabelText("Back to bottom")).toBeInTheDocument();

    rerender(
      <MessageList
        {...baseProps([msg("m1", "olá")])}
        threadId="t-load2"
        isLoadingThread
      />,
    );
    expect(screen.queryByLabelText("Back to bottom")).not.toBeInTheDocument();
  });
});

describe("MessageList — thread virtualizada (> 50 mensagens)", () => {
  it("usa virtualizer.scrollToIndex pro último item, não scrollTop bruto", async () => {
    vi.useFakeTimers();
    const manyMessages = Array.from({ length: 80 }, (_, i) =>
      msg(`m${i}`, `mensagem ${i}`),
    );
    render(<MessageList {...baseProps(manyMessages)} threadId="t-virt" />);

    const container = screen.getByLabelText("Messages");
    mockScrollMetrics(container);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(scrollToIndexMock).toHaveBeenCalled();
    expect(scrollToIndexMock).toHaveBeenLastCalledWith(79, { align: "end" });
  });
});

describe("MessageList — padding compacto (modo IDE)", () => {
  it("compact=true usa px-3 em vez de px-4/sm:px-6", () => {
    const { container } = render(
      <MessageList
        {...baseProps([msg("m1", "olá")])}
        threadId="t-compact"
        compact
      />,
    );

    expect(container.querySelector(".px-3")).not.toBeNull();
    expect(container.querySelector(".px-4")).toBeNull();
  });

  it("erro/borda: compact=false (padrão) preserva o padding original", () => {
    const { container } = render(
      <MessageList
        {...baseProps([msg("m1", "olá")])}
        threadId="t-nao-compacto"
      />,
    );

    expect(container.querySelector(".px-4")).not.toBeNull();
    expect(container.querySelector(".px-3")).toBeNull();
  });
});

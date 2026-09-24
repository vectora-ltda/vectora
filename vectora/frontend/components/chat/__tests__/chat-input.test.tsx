// @vitest-environment jsdom
/**
 * Tests para ChatInput: textarea controlado, botão de enviar (habilita só com
 * texto + usuário online) e callback onSend. Cobre o layout com o botão de
 * enviar dentro da linha do input.
 */

import { describe, expect, it, afterEach, beforeEach, vi } from "vitest";
import {
  render as rtlRender,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ChatInput, isComposerCompactWidth } from "../chat-input";
import { m } from "@/lib/paraglide/messages";
import { checkOpenRouterModelSupportsImage } from "@/lib/api/openrouter-vision";

vi.mock("@/lib/api/openrouter-vision", () => ({
  checkOpenRouterModelSupportsImage: vi.fn(),
}));

describe("ChatInput — breakpoint do container", () => {
  it("usa o modo compacto abaixo do breakpoint @md de 28rem", () => {
    expect(isComposerCompactWidth(28 * 16 - 1, 16)).toBe(true);
    expect(isComposerCompactWidth(28 * 16, 16)).toBe(false);
    expect(isComposerCompactWidth(28 * 18 - 1, 18)).toBe(true);
  });
});

// Estado mockável para o settings store — cobre ChatInput e EffortMenu.
const mockSettings = {
  chatMode: false,
  setChatMode: vi.fn(),
  uiMode: "assistant",
  setUiMode: vi.fn(),
  reasoningEffort: "medium" as const,
  showToolCalls: false,
  permissionMode: "ask" as const,
  setReasoningEffort: vi.fn(),
  setShowToolCalls: vi.fn(),
  setPermissionMode: vi.fn(),
};

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (selector?: (s: typeof mockSettings) => unknown) =>
    selector ? selector(mockSettings) : mockSettings,
  PERMISSION_MODES: ["ask", "accept_edits", "plan", "auto", "bypass"],
  REASONING_EFFORTS: ["low", "medium", "high", "max"],
}));

const mockWsState = {
  workspaces: [],
  active_id: null,
  status: "idle" as const,
  error: null,
  getActive: () => null,
  setActive: vi.fn(),
  addWorkspace: vi.fn(),
  removeWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
  hydrate: vi.fn(),
  trust: vi.fn(),
};

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (s: typeof mockWsState) => unknown) =>
    selector(mockWsState),
}));

afterEach(() => {
  cleanup();
  mockSettings.chatMode = false;
  mockSettings.setChatMode.mockReset();
});

// ChatInput usa Tooltip — precisa do provider no entorno.
function render(ui: React.ReactElement) {
  return rtlRender(<TooltipProvider>{ui}</TooltipProvider>);
}

type Props = Parameters<typeof ChatInput>[0];

function baseProps(over: Partial<Props> = {}): Props {
  return {
    input: "",
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onKeyDown: vi.fn(),
    isLoading: false,
    isStopping: false,
    onStop: vi.fn(),
    userId: "u1",
    attachedFiles: [],
    uploadError: null,
    inputError: null,
    isDragging: false,
    onDragOver: vi.fn(),
    onDragLeave: vi.fn(),
    onDrop: vi.fn(),
    onPaste: vi.fn(),
    onRemoveFile: vi.fn(),
    onFileButtonClick: vi.fn(),
    fileInputRef: { current: null },
    onFileSelect: vi.fn(),
    ...over,
  } as Props;
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: m.tooltip_chat_send(),
  }) as HTMLButtonElement;
}

describe("ChatInput", () => {
  it("renderiza o textarea de mensagem", () => {
    render(<ChatInput {...baseProps()} />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("desabilita o enviar quando o input está vazio", () => {
    render(<ChatInput {...baseProps({ input: "" })} />);
    expect(sendButton()).toBeDisabled();
  });

  it("habilita o enviar quando há texto", () => {
    render(<ChatInput {...baseProps({ input: "olá" })} />);
    expect(sendButton()).not.toBeDisabled();
  });

  it("clicar em enviar chama onSend", () => {
    const onSend = vi.fn();
    render(<ChatInput {...baseProps({ input: "oi", onSend })} />);
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("digitar no textarea chama onInputChange", () => {
    const onInputChange = vi.fn();
    render(<ChatInput {...baseProps({ onInputChange })} />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "novo" },
    });
    expect(onInputChange).toHaveBeenCalledWith("novo");
  });

  // sidebar-mode-toggle é o ponto canônico do toggle de Modo Chat, não a AppBar.
  it("toggle data-chatmode não existe na AppBar (use a sidebar)", () => {
    render(<ChatInput {...baseProps()} />);
    expect(document.querySelector("[data-chatmode]")).toBeNull();
  });

  it("em chatMode=true WorkspaceSelector não está no DOM", () => {
    mockSettings.chatMode = true;
    render(<ChatInput {...baseProps()} />);
    expect(
      document.querySelector("[data-testid='workspace-selector']"),
    ).toBeNull();
  });

  it("em code mode (chatMode=false) WorkspaceSelector não está na AppBar", () => {
    // O workspace é imutável após iniciar a conversa — escolhido só no modal.
    mockSettings.chatMode = false;
    render(<ChatInput {...baseProps()} />);
    expect(
      document.querySelector("[data-testid='workspace-selector']"),
    ).toBeNull();
  });

  // ── erros / fila / botão VS Code (render direto da ChatInput) ────────────────

  it("exibe uploadError quando presente", () => {
    render(
      <ChatInput {...baseProps({ uploadError: "Arquivo grande demais" })} />,
    );
    expect(screen.getByText("Arquivo grande demais")).toBeTruthy();
  });

  it("não exibe bloco de uploadError quando null", () => {
    render(<ChatInput {...baseProps({ uploadError: null })} />);
    expect(screen.queryByText(/demais/)).toBeNull();
  });

  it("exibe voiceError quando presente", () => {
    render(
      <ChatInput {...baseProps({ voiceError: "Microfone indisponível" })} />,
    );
    expect(screen.getByText("Microfone indisponível")).toBeTruthy();
  });

  it("renderiza mensagens enfileiradas", () => {
    render(
      <ChatInput
        {...baseProps({
          queuedMessages: [
            { id: "q1", content: "primeira na fila" },
            { id: "q2", content: "segunda na fila" },
          ],
        })}
      />,
    );
    expect(screen.getByText("primeira na fila")).toBeTruthy();
    expect(screen.getByText("segunda na fila")).toBeTruthy();
  });

  it("sem mensagens enfileiradas não renderiza a fila", () => {
    render(<ChatInput {...baseProps({ queuedMessages: [] })} />);
    expect(screen.queryByText(/na fila/)).toBeNull();
  });

  it("botão VS Code aparece em code mode com workspace ativo", () => {
    mockSettings.chatMode = false;
    mockWsState.getActive = () => ({ id: "ws1" }) as never;
    try {
      render(<ChatInput {...baseProps()} />);
      expect(screen.queryByLabelText(m.workbench_open_vscode())).toBeTruthy();
    } finally {
      mockWsState.getActive = () => null;
    }
  });

  it("botão VS Code NÃO aparece em chat mode mesmo com workspace", () => {
    mockSettings.chatMode = true;
    mockWsState.getActive = () => ({ id: "ws1" }) as never;
    try {
      render(<ChatInput {...baseProps()} />);
      expect(screen.queryByLabelText(m.workbench_open_vscode())).toBeNull();
    } finally {
      mockWsState.getActive = () => null;
      mockSettings.chatMode = false;
    }
  });

  it("botão VS Code NÃO aparece sem workspace ativo", () => {
    mockSettings.chatMode = false;
    render(<ChatInput {...baseProps()} />);
    expect(screen.queryByLabelText(m.workbench_open_vscode())).toBeNull();
  });

  it("enviar fica desabilitado sem userId", () => {
    render(<ChatInput {...baseProps({ input: "oi", userId: null })} />);
    expect(sendButton().disabled).toBe(true);
  });

  it("botão VS Code aparece no modo IDE com workspace ativo", () => {
    mockSettings.chatMode = false;
    mockSettings.uiMode = "ide";
    mockWsState.getActive = () => ({ id: "ws1" }) as never;
    try {
      render(<ChatInput {...baseProps()} />);
      expect(screen.queryByLabelText(m.workbench_open_vscode())).toBeTruthy();
    } finally {
      mockWsState.getActive = () => null;
      mockSettings.uiMode = "assistant";
    }
  });
});

describe("ChatInput — aviso de modelo sem suporte a imagem", () => {
  const imageFile = {
    id: "f1",
    mimeType: "image/png",
    base64: "aGVsbG8=",
    name: "foto.png",
  };

  it("mostra aviso quando há imagem anexada e o modelo é Cohere (sem visão)", () => {
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [imageFile],
          agentConfig: { model: "cohere:command-a-03-2025" },
        })}
      />,
    );
    expect(
      screen.getByText(m.chat_input_no_vision_warning()),
    ).toBeInTheDocument();
  });

  it("não mostra aviso quando o modelo suporta imagem (Gemini)", () => {
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [imageFile],
          agentConfig: { model: "google-genai:gemini-2.5-flash" },
        })}
      />,
    );
    expect(
      screen.queryByText(m.chat_input_no_vision_warning()),
    ).not.toBeInTheDocument();
  });

  it("não mostra aviso sem imagem anexada, mesmo com modelo Cohere", () => {
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [],
          agentConfig: { model: "cohere:command-a-03-2025" },
        })}
      />,
    );
    expect(
      screen.queryByText(m.chat_input_no_vision_warning()),
    ).not.toBeInTheDocument();
  });

  it("não mostra aviso quando o anexo não é imagem (ex: código)", () => {
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [
            { id: "f2", mimeType: "text/x-python", name: "script.py" },
          ],
          agentConfig: { model: "cohere:command-a-03-2025" },
        })}
      />,
    );
    expect(
      screen.queryByText(m.chat_input_no_vision_warning()),
    ).not.toBeInTheDocument();
  });

  // Responsividade por CONTAINER (não por viewport): no modo IDE o ChatInput
  // vive numa sidebar estreita enquanto a janela segue larga, então o rodapé
  // precisa reagir à largura do próprio composer via container queries do
  // Tailwind v4, não a breakpoints `sm:` de viewport.
  it("o rodapé usa o container do composer sem overflow horizontal", () => {
    const { container } = render(
      <ChatInput
        {...baseProps({
          agentConfig: { model: "openrouter:openai/gpt-4o" },
          onAgentConfigChange: vi.fn(),
          modelId: "openrouter:openai/gpt-4o",
        })}
      />,
    );

    // O wrapper do composer estabelece o contexto de container nomeado.
    expect(container.querySelector(".\\@container\\/composer")).not.toBeNull();

    // O rodapé permanece em uma única linha; os controles internos cedem
    // largura e truncam seus rótulos quando a coluna fica estreita.
    const footer = container.querySelector('[data-testid="chat-input-footer"]');
    expect(footer).not.toBeNull();
    expect(footer).toHaveClass("flex-nowrap");
    expect(footer).not.toHaveClass("flex-wrap");

    // Nenhum breakpoint de viewport (`sm:`) deve sobrar no rodapé — só container.
    expect(container.querySelector(".sm\\:flex-nowrap")).toBeNull();

    // Em containers estreitos os rótulos cedem espaço, mas os botões continuam
    // identificáveis por acessibilidade e tooltip.
    expect(
      container.querySelectorAll('[aria-expanded="false"]').length,
    ).toBeGreaterThanOrEqual(2);
    expect(
      container.querySelectorAll("button[aria-label]").length,
    ).toBeGreaterThan(0);
  });

  it("mantém modelo e limite de contexto juntos no modo wide", () => {
    const { container } = render(
      <ChatInput
        {...baseProps({
          agentConfig: { model: "openrouter:openai/gpt-4o" },
          onAgentConfigChange: vi.fn(),
          modelId: "openrouter:openai/gpt-4o",
        })}
      />,
    );
    const modelControls = container.querySelector(
      '[data-testid="wide-model-controls"]',
    );

    expect(modelControls).toHaveClass("w-fit");
    expect(modelControls).toHaveClass("max-w-full");
    expect(modelControls).toHaveClass("flex-[0_1_auto]");
    expect(modelControls).not.toHaveClass("flex-[1_1_auto]");

    const controlGroup = container.querySelector(
      '[data-testid="wide-control-group"]',
    );
    expect(controlGroup).toHaveClass("gap-2");
    expect(controlGroup).not.toHaveClass("justify-between");
    expect(modelControls?.querySelector("button")).toBeInTheDocument();
  });

  it("mede os grupos wide e redistribui após o ResizeObserver", () => {
    let groupWidth = 200;
    const resizeCallbacks: ResizeObserverCallback[] = [];
    const OriginalResizeObserver = globalThis.ResizeObserver;
    const originalClientWidth = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "clientWidth",
    );
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          resizeCallbacks.push(callback);
        }
        observe() {}
        disconnect() {}
      },
    );
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get() {
        if (this.getAttribute("data-testid") === "wide-control-group") {
          return groupWidth;
        }
        return typeof this.className === "string" &&
          this.className.includes("composer")
          ? 640
          : 0;
      },
    });
    const bounds = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        const width =
          typeof this.className === "string" &&
          this.className.includes("composer")
            ? 640
            : 100;
        return {
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: width,
          bottom: 24,
          width,
          height: 24,
          toJSON: () => ({}),
        };
      });

    try {
      const { container } = render(
        <ChatInput
          {...baseProps({
            agentConfig: { model: "openrouter:openai/gpt-4o" },
            onAgentConfigChange: vi.fn(),
            modelId: "openrouter:openai/gpt-4o",
          })}
        />,
      );
      const group = container.querySelector(
        '[data-testid="wide-control-group"]',
      )!;
      const initialWidths = [...group.children].map((item) =>
        Number.parseFloat((item as HTMLElement).style.width),
      );
      expect(initialWidths.some((width) => width > 0)).toBe(true);

      groupWidth = 320;
      for (const callback of resizeCallbacks)
        callback([], {} as ResizeObserver);

      const expandedWidths = [...group.children].map((item) =>
        Number.parseFloat((item as HTMLElement).style.width),
      );
      expect(
        expandedWidths.some((width, index) => width > initialWidths[index]!),
      ).toBe(true);
    } finally {
      bounds.mockRestore();
      vi.unstubAllGlobals();
      if (originalClientWidth) {
        Object.defineProperty(
          HTMLElement.prototype,
          "clientWidth",
          originalClientWidth,
        );
      }
      if (OriginalResizeObserver)
        vi.stubGlobal("ResizeObserver", OriginalResizeObserver);
    }
  });

  it("mantém acesso horizontal ao texto longo no input compacto", () => {
    const { container } = render(
      <ChatInput
        {...baseProps({
          compact: true,
          input: "uma mensagem muito longa que precisa continuar acessível",
        })}
      />,
    );

    const textarea = screen.getByRole("textbox");
    expect(textarea.className).toContain("overflow-x-auto");
    expect(textarea.className).toContain("overflow-y-hidden");

    const footer = container.querySelector('[data-testid="chat-input-footer"]');
    expect(footer).toHaveClass("px-1.5", "py-2", "gap-x-1.5");
    expect(screen.getByTestId("plus-menu-trigger")).toHaveClass(
      "h-6",
      "w-auto",
    );
    expect(
      screen.getByTestId("plus-menu-trigger").querySelector("svg"),
    ).toHaveClass("size-3");
  });

  it("preserva o estado de erro sem criar overflow no composer compacto", () => {
    const { container } = render(
      <ChatInput
        {...baseProps({ compact: true, inputError: "Mensagem indisponível" })}
      />,
    );

    expect(screen.getByText("Mensagem indisponível")).toBeInTheDocument();
    expect(
      container.querySelector('[data-testid="chat-input-footer"]'),
    ).toHaveClass("min-w-0");
  });
});

describe("ChatInput — capability de imagem no OpenRouter varia por modelo", () => {
  const imageFile = {
    id: "f1",
    mimeType: "image/png",
    base64: "aGVsbG8=",
    name: "foto.png",
  };

  it("consulta o catálogo pelo id do modelo sem o prefixo do provedor", async () => {
    vi.mocked(checkOpenRouterModelSupportsImage).mockResolvedValue(true);
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [imageFile],
          agentConfig: { model: "openrouter:openai/gpt-4o" },
        })}
      />,
    );
    await waitFor(() => {
      expect(checkOpenRouterModelSupportsImage).toHaveBeenCalledWith(
        "openai/gpt-4o",
      );
    });
  });

  it("não mostra aviso quando o modelo OpenRouter suporta imagem", async () => {
    vi.mocked(checkOpenRouterModelSupportsImage).mockResolvedValue(true);
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [imageFile],
          agentConfig: { model: "openrouter:openai/gpt-4o" },
        })}
      />,
    );
    await waitFor(() => {
      expect(checkOpenRouterModelSupportsImage).toHaveBeenCalled();
    });
    expect(
      screen.queryByText(m.chat_input_no_vision_warning()),
    ).not.toBeInTheDocument();
  });

  it("mostra aviso quando o modelo OpenRouter não suporta imagem", async () => {
    vi.mocked(checkOpenRouterModelSupportsImage).mockResolvedValue(false);
    render(
      <ChatInput
        {...baseProps({
          attachedFiles: [imageFile],
          agentConfig: { model: "openrouter:some-text-only-model" },
        })}
      />,
    );
    expect(
      await screen.findByText(m.chat_input_no_vision_warning()),
    ).toBeInTheDocument();
  });
});

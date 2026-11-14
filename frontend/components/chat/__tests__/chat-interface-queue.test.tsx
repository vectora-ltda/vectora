// @vitest-environment jsdom
/**
 * Fluxo da fila do composer dentro do `ChatInterface` real: envio imediato
 * depois da resposta final (sem esperar timer) e falha de rede com retry.
 * O stream e os stores são simulados; o reducer de estado e a fila são os reais.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import { ChatInterface } from "../chat-interface";

type StreamResult = {
  assistantContent: string;
  requestReachedBackend: boolean;
};

const h = vi.hoisted(() => ({
  chatInputProps: null as any,
  streams: [] as {
    args: unknown[];
    resolve: (value: StreamResult) => void;
  }[],
  processStream: null as any,
  messages: [] as unknown[],
  setMessages: vi.fn(),
  files: [] as unknown[],
  fileApi: {
    removeFile: vi.fn(),
    handleFileSelect: vi.fn(),
    handlePaste: vi.fn(),
    handleDrop: vi.fn(),
    handleDragOver: vi.fn(),
    handleDragLeave: vi.fn(),
    setUploadError: vi.fn(),
    processFiles: vi.fn(),
    clearFiles: vi.fn(),
    replaceFiles: vi.fn(),
  },
  voiceApi: {
    isListening: false,
    startListening: vi.fn(),
    stopListening: vi.fn(),
  },
  streamApi: {
    processResume: vi.fn(),
    abort: vi.fn(),
  },
}));

vi.mock("@/src/router", () => ({
  queryClient: { setQueryData: vi.fn(), getQueryData: vi.fn() },
}));
vi.mock("@/lib/queries/threads", () => ({
  threadsQueryKey: () => ["threads"],
}));
vi.mock("@/lib/api/vectora-client", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getHistory: vi.fn().mockResolvedValue({ messages: [] }),
  getThread: vi.fn().mockResolvedValue({ workspace_id: null }),
  markThreadRead: vi
    .fn()
    .mockResolvedValue({ thread_id: "t", unread_count: 0 }),
  cancelRun: vi.fn().mockResolvedValue(undefined),
  generateTitle: vi.fn().mockResolvedValue({ title: "" }),
}));
vi.mock("@/lib/hooks/chat/use-thread-messages", () => ({
  useThreadMessages: () => [h.messages, h.setMessages],
}));
vi.mock("@/lib/hooks/auth", () => ({ useUserId: () => "user-1" }));
vi.mock("@/lib/hooks/chat", async () => {
  const actual = await vi.importActual<any>("@/lib/hooks/chat/use-chat-state");
  return {
    useStreamHandler: () => ({
      processStream: h.processStream,
      ...h.streamApi,
    }),
    useFeedback: () => ({ feedback: null, submitFeedback: vi.fn() }),
    useChatState: actual.useChatState,
  };
});
vi.mock("@/lib/hooks/files", () => ({
  useFileUpload: () => ({ attachedFiles: h.files, ...h.fileApi }),
  useVoiceInput: () => h.voiceApi,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: () => ({ activeWorkspace: null }),
}));
vi.mock("@/lib/stores/chat-input-store", () => {
  const state = {
    draft: null,
    consumeDraft: vi.fn(),
    mention: null,
    consumeMention: vi.fn(),
    getDraft: vi.fn(() => ""),
    setDraft: vi.fn(),
    clearDraft: vi.fn(),
    getFileDraft: vi.fn(() => []),
    setFileDraft: vi.fn(),
  };
  return {
    useChatInputStore: Object.assign(
      (selector?: (s: any) => any) => (selector ? selector(state) : state),
      { getState: () => state },
    ),
  };
});
vi.mock("@/lib/stores/threads-store", () => ({
  useThreadsStore: Object.assign(() => ({}), {
    getState: () => ({ cache: {} }),
  }),
}));
vi.mock("@/lib/stores/streaming-store", () => ({
  useStreamingStore: Object.assign(() => false, {
    getState: () => ({ setStreaming: vi.fn() }),
  }),
}));
vi.mock("@/lib/stores/workbench-store", () => ({
  useWorkbenchStore: Object.assign(() => ({}), {
    getState: () => ({ setTodos: vi.fn() }),
  }),
}));
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: () => ({ lang: "pt" }),
}));
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: Object.assign(() => ({ addToast: vi.fn() }), {
    getState: () => ({ warning: vi.fn(), addToast: vi.fn() }),
  }),
}));
vi.mock("../features/empty-state-header", () => ({
  EmptyStateHeader: () => <div />,
}));
vi.mock("../message-list", () => ({ MessageList: () => <div /> }));
vi.mock("../chat-input", () => ({
  ChatInput: (props: unknown) => {
    h.chatInputProps = props;
    return <div data-testid="chat-input" />;
  },
}));

const flush = () => act(async () => {});

async function submit(text: string) {
  await act(async () => {
    h.chatInputProps.onInputChange(text);
  });
  // O envio só termina com o stream; não esperamos por ele.
  await act(async () => {
    void h.chatInputProps.onSend();
  });
  await flush();
}

const queue = () =>
  (
    h.chatInputProps.queuedMessages as { content: string; status: string }[]
  ).map((item) => `${item.content}:${item.status}`);

describe("ChatInterface — fila do composer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.streams = [];
    h.messages = [];
    h.setMessages.mockReset();
    h.processStream = vi.fn(
      (...args: unknown[]) =>
        new Promise<StreamResult>((resolve) => {
          h.streams.push({ args, resolve });
        }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("envia na hora depois da resposta final, sem esperar nenhum timer", async () => {
    render(<ChatInterface threadId="thread-q" />);
    await flush();

    await submit("primeira");
    expect(h.processStream).toHaveBeenCalledTimes(1);

    await act(async () => {
      h.streams[0].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();

    // Nenhum timer foi avançado: o composer já está livre.
    await submit("segunda");
    expect(h.processStream).toHaveBeenCalledTimes(2);
    expect(h.processStream.mock.calls[1][0]).toBe("segunda");
    expect(queue()).toEqual([]);
  });

  it("falha de rede mantém o item na fila e o retry reenvia com o mesmo turn_id", async () => {
    render(<ChatInterface threadId="thread-q" />);
    await flush();

    await submit("primeira");
    await submit("na fila");
    // Enquanto a primeira responde, a segunda espera.
    expect(h.processStream).toHaveBeenCalledTimes(1);
    expect(queue()).toEqual(["na fila:waiting"]);

    await act(async () => {
      h.streams[0].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();
    expect(h.processStream).toHaveBeenCalledTimes(2);
    expect(queue()).toEqual(["na fila:sending"]);
    const turnId = h.streams[1].args[4];

    // A requisição não chegou ao servidor: o item fica como falhou.
    await act(async () => {
      h.streams[1].resolve({
        assistantContent: "",
        requestReachedBackend: false,
      });
    });
    await flush();
    expect(queue()).toEqual(["na fila:failed"]);
    expect(h.processStream).toHaveBeenCalledTimes(2);

    // Retry: mesmo turn_id, e só sai da fila quando o servidor confirma.
    const [item] = h.chatInputProps.queuedMessages as { id: string }[];
    await act(async () => {
      h.chatInputProps.onRetryQueued(item.id);
    });
    await flush();
    expect(h.processStream).toHaveBeenCalledTimes(3);
    expect(h.streams[2].args[4]).toBe(turnId);
    expect(queue()).toEqual(["na fila:sending"]);

    await act(async () => {
      h.streams[2].resolve({
        assistantContent: "feito",
        requestReachedBackend: true,
      });
    });
    await flush();
    expect(queue()).toEqual([]);
  });

  it("alerta do sistema uma vez por turno quando a aba está oculta e a resposta demorou", async () => {
    sessionStorage.clear();
    const alerts = vi.fn();
    vi.stubGlobal(
      "Notification",
      Object.assign(alerts, { permission: "granted" }),
    );
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    render(<ChatInterface threadId="thread-alerta" />);
    await flush();

    await submit("primeira");
    await act(async () => {
      vi.advanceTimersByTime(20_000);
      h.streams[0].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();
    expect(alerts).toHaveBeenCalledTimes(1);

    // Outro turno é outra chave: alerta de novo; o mesmo turno não repete.
    await submit("segunda");
    await act(async () => {
      vi.advanceTimersByTime(20_000);
      h.streams[1].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();
    expect(alerts).toHaveBeenCalledTimes(2);

    visibility.mockRestore();
    vi.unstubAllGlobals();
  });

  it("sem permissão do sistema não há alerta e a chave não é consumida", async () => {
    sessionStorage.clear();
    const alerts = vi.fn();
    vi.stubGlobal(
      "Notification",
      Object.assign(alerts, { permission: "denied" }),
    );
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    render(<ChatInterface threadId="thread-alerta" />);
    await flush();

    await submit("primeira");
    await act(async () => {
      vi.advanceTimersByTime(20_000);
      h.streams[0].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();

    expect(alerts).not.toHaveBeenCalled();
    expect(
      sessionStorage.getItem("vectora:delivered-notifications"),
    ).toBeNull();
    visibility.mockRestore();
    vi.unstubAllGlobals();
  });

  it("interromper o envio marca o item como cancelado e a fila segue para o próximo", async () => {
    render(<ChatInterface threadId="thread-q" />);
    await flush();

    await submit("primeira");
    await submit("vai cancelar");
    await submit("depois");
    await act(async () => {
      h.streams[0].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();
    expect(queue()).toEqual(["vai cancelar:sending", "depois:waiting"]);

    // O usuário para o envio antes de a requisição chegar ao servidor.
    await act(async () => {
      await h.chatInputProps.onStop();
    });
    await act(async () => {
      h.streams[1].resolve({
        assistantContent: "",
        requestReachedBackend: false,
      });
    });
    await flush();

    expect(queue()[0]).toBe("vai cancelar:cancelled");
    // O cancelado não bloqueia: o seguinte já está em envio.
    expect(h.processStream).toHaveBeenCalledTimes(3);
    expect(h.processStream.mock.calls[2][0]).toBe("depois");
    expect(queue()).toEqual(["vai cancelar:cancelled", "depois:sending"]);

    // Reenviar o cancelado reaproveita o mesmo turn_id.
    const turnId = h.streams[1].args[4];
    await act(async () => {
      h.streams[2].resolve({
        assistantContent: "ok",
        requestReachedBackend: true,
      });
    });
    await flush();
    const [item] = h.chatInputProps.queuedMessages as { id: string }[];
    await act(async () => {
      h.chatInputProps.onRetryQueued(item.id);
    });
    await flush();
    expect(h.streams[3].args[4]).toBe(turnId);
  });

  it("envio direto que não chega ao servidor devolve texto ao composer e tira a mensagem do chat", async () => {
    let chatState: { id: string }[] = [];
    h.setMessages.mockImplementation((update: unknown) => {
      chatState =
        typeof update === "function"
          ? (update as (prev: unknown[]) => { id: string }[])(chatState)
          : (update as { id: string }[]);
    });
    render(<ChatInterface threadId="thread-q" />);
    await flush();

    await act(async () => {
      h.chatInputProps.onInputChange("não vai chegar");
    });
    await act(async () => {
      void h.chatInputProps.onSend();
    });
    await flush();
    expect(h.processStream).toHaveBeenCalledTimes(1);
    expect(chatState).toHaveLength(1);

    await act(async () => {
      h.streams[0].resolve({
        assistantContent: "",
        requestReachedBackend: false,
      });
    });
    await flush();

    expect(h.chatInputProps.input).toBe("não vai chegar");
    // O envio falho não deixa a mensagem nem a bolha de erro no chat.
    expect(chatState).toEqual([]);
  });

  it("não sobrescreve o que o usuário já digitou enquanto o envio falhava", async () => {
    render(<ChatInterface threadId="thread-q" />);
    await flush();

    await act(async () => {
      h.chatInputProps.onInputChange("primeira");
    });
    await act(async () => {
      void h.chatInputProps.onSend();
    });
    await flush();
    await act(async () => {
      h.chatInputProps.onInputChange("já estou digitando outra");
    });
    await act(async () => {
      h.streams[0].resolve({
        assistantContent: "",
        requestReachedBackend: false,
      });
    });
    await flush();

    expect(h.chatInputProps.input).toBe("já estou digitando outra");
  });
});

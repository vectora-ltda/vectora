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
});

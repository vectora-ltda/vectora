// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import { ChatInterface } from "../chat-interface";
import * as vectoraClient from "@/lib/api/vectora-client";

vi.mock("@/src/router", () => ({
  queryClient: {
    setQueryData: vi.fn(),
    getQueryData: vi.fn(),
  },
}));

vi.mock("@/lib/queries/threads", () => ({
  threadsQueryKey: () => ["threads"],
}));

vi.mock("@/lib/hooks/chat/use-thread-messages", () => ({
  useThreadMessages: () => [
    [
      {
        id: "msg-1",
        role: "assistant",
        content: "Olá",
        persistedMessageId: 10,
      },
    ],
    vi.fn(),
  ],
}));

vi.mock("@/lib/hooks/auth", () => ({
  useUserId: () => "user-1",
}));

vi.mock("@/lib/hooks/chat", () => ({
  useStreamHandler: () => ({
    sendMessage: vi.fn(),
    abort: vi.fn(),
    isStreaming: false,
    streamingContent: "",
    error: null,
  }),
  useFeedback: () => ({
    feedback: null,
    submitFeedback: vi.fn(),
  }),
  useChatState: () => ({
    state: {
      input: "",
      isLoading: false,
      isRegenerating: false,
      isTyping: false,
      sendingMessage: false,
      selectedMessageId: null,
      messageScrollIndex: null,
    },
    dispatch: vi.fn(),
    setInput: vi.fn(),
  }),
}));

vi.mock("@/lib/hooks/chat/use-send-guard", () => ({
  useSendGuard: () => ({
    canSend: true,
  }),
}));

vi.mock("@/lib/hooks/files", () => ({
  useFileUpload: () => ({
    files: [],
    uploadFile: vi.fn(),
    removeFile: vi.fn(),
  }),
  useVoiceInput: () => ({
    isListening: false,
    startListening: vi.fn(),
    stopListening: vi.fn(),
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: () => ({
    activeWorkspace: null,
  }),
}));

vi.mock("@/lib/stores/chat-input-store", () => ({
  useChatInputStore: (selector?: (s: any) => any) => {
    const state = {
      input: "",
      setInput: vi.fn(),
      draft: null,
      consumeDraft: vi.fn(),
      mention: null,
      consumeMention: vi.fn(),
    };
    return selector ? selector(state) : state;
  },
}));

const { mockThreadsStore } = vi.hoisted(() => ({
  mockThreadsStore: Object.assign(() => ({}), {
    getState: () => ({
      cache: {
        "thread-abc": {
          messages: [{ id: "msg-1" }],
        },
      },
    }),
  }),
}));

vi.mock("@/lib/stores/threads-store", () => ({
  useThreadsStore: mockThreadsStore,
}));

vi.mock("@/lib/stores/streaming-store", () => ({
  useStreamingStore: () => false,
}));

vi.mock("@/lib/stores/workbench-store", () => ({
  useWorkbenchStore: () => ({}),
}));

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: () => ({
    lang: "pt",
  }),
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: () => ({
    addToast: vi.fn(),
  }),
}));

vi.mock("../message-list", () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock("../chat-input", () => ({
  ChatInput: () => <div data-testid="chat-input" />,
}));

describe("ChatInterface - readGenerationRef unmount isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("descarta retries e mutações quando a requisição de markThreadRead falha após o unmount", async () => {
    let rejectPromise!: (err: unknown) => void;
    const markPromise = new Promise<{
      thread_id: string;
      unread_count: number;
    }>((_, reject) => {
      rejectPromise = reject;
    });

    vi.spyOn(vectoraClient, "markThreadRead").mockReturnValue(markPromise);

    const { unmount } = render(<ChatInterface threadId="thread-abc" />);

    // markThreadRead foi disparado
    expect(vectoraClient.markThreadRead).toHaveBeenCalledWith("thread-abc", 10);

    // Desmonta o componente enquanto a requisição ainda está pendente
    unmount();

    // Agora a requisição é rejeitada
    await act(async () => {
      rejectPromise(new Error("Network error"));
    });

    // Nenhum timer de retry deve ser agendado nem disparar nova tentativa
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    // markThreadRead não deve ter sido chamado uma segunda vez
    expect(vectoraClient.markThreadRead).toHaveBeenCalledTimes(1);
  });
});

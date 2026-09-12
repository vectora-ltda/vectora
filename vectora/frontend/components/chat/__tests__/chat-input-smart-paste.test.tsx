// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, property) => () => String(property) }),
}));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (select: (state: object) => unknown) =>
    select({ getActive: () => null }),
}));
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (select: (state: object) => unknown) =>
    select({ chatMode: true, uiMode: "assistant" }),
}));
vi.mock("@/lib/hooks/use-network-status", () => ({
  useNetworkStatus: () => ({ offline: false }),
}));
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ error: vi.fn() }) },
}));
vi.mock("@/lib/config/deployment-config", () => ({
  getModelProvider: () => undefined,
  isProviderVisionCapable: () => true,
}));
vi.mock("@/lib/api/openrouter-vision", () => ({
  checkOpenRouterModelSupportsImage: vi.fn(),
}));
vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));
vi.mock("@/components/ui/textarea", () => ({
  Textarea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
    <textarea {...props} />
  ),
}));
vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));
vi.mock("../features/file-preview-grid", () => ({
  FilePreviewGrid: () => null,
}));
vi.mock("../features/url-preview-card", () => ({
  UrlPreviewCard: () => null,
}));
vi.mock("../features/voice-input-button", () => ({
  VoiceInputButton: () => null,
}));
vi.mock("../features/permission-mode-menu", () => ({
  PermissionModeMenu: () => null,
}));
vi.mock("../features/effort-menu", () => ({ EffortMenu: () => null }));
vi.mock("../features/plus-menu", () => ({ PlusMenu: () => null }));
vi.mock("../features/usage-popover", () => ({ UsagePopover: () => null }));
vi.mock("../features/slash-command-menu", () => ({
  SlashCommandMenu: () => null,
}));
vi.mock("../features/at-mention-menu", () => ({ AtMentionMenu: () => null }));
vi.mock("../model-selector", () => ({ ModelSelector: () => null }));
vi.mock("@/components/icons/vscode-icon", () => ({ VscodeIcon: () => null }));

import { ChatInput } from "../chat-input";

afterEach(cleanup);

function renderStructuredPaste() {
  const callbacks = {
    attach: vi.fn(),
    text: vi.fn(),
    cancel: vi.fn(),
  };

  function StructuredPasteHarness() {
    const [input, setInput] = useState("texto existente");
    const [structuredPaste, setStructuredPaste] = useState<{
      content: string;
      extension: string;
      mimeType: string;
    } | null>({
      content: '{"ok":true}',
      extension: "json",
      mimeType: "application/json",
    });

    return (
      <ChatInput
        input={input}
        onInputChange={setInput}
        onSend={vi.fn()}
        onKeyDown={vi.fn()}
        isLoading={false}
        isStopping={false}
        onStop={vi.fn()}
        userId="user-1"
        attachedFiles={[]}
        uploadError={null}
        inputError={null}
        isDragging={false}
        onDragOver={vi.fn()}
        onDragLeave={vi.fn()}
        onDrop={vi.fn()}
        onPaste={vi.fn()}
        onRemoveFile={vi.fn()}
        onFileButtonClick={vi.fn()}
        fileInputRef={{ current: null }}
        onFileSelect={vi.fn()}
        structuredPaste={structuredPaste}
        onStructuredPasteAttach={async () => {
          callbacks.attach();
          setStructuredPaste(null);
        }}
        onStructuredPasteText={() => {
          if (!structuredPaste) return;
          callbacks.text();
          setInput((current) => `${current}\n${structuredPaste.content}`);
          setStructuredPaste(null);
        }}
        onStructuredPasteCancel={() => {
          callbacks.cancel();
          setStructuredPaste(null);
        }}
      />
    );
  }

  render(<StructuredPasteHarness />);
  return callbacks;
}

describe("ChatInput structured paste contract", () => {
  it("preserves the draft when the user attaches the structured paste", () => {
    const callbacks = renderStructuredPaste();

    expect(screen.getByTestId("chat-input")).toHaveValue("texto existente");
    fireEvent.click(screen.getByText("chat_structured_paste_attach"));

    expect(callbacks.attach).toHaveBeenCalledOnce();
    expect(screen.getByTestId("chat-input")).toHaveValue("texto existente");
  });

  it("preserves the draft when the user cancels the structured paste", () => {
    const callbacks = renderStructuredPaste();

    fireEvent.click(screen.getByText("chat_structured_paste_cancel"));

    expect(callbacks.cancel).toHaveBeenCalledOnce();
    expect(screen.getByTestId("chat-input")).toHaveValue("texto existente");
  });

  it("appends the structured paste when the user chooses text", () => {
    const callbacks = renderStructuredPaste();

    fireEvent.click(screen.getByText("chat_structured_paste_text"));

    expect(callbacks.text).toHaveBeenCalledOnce();
    expect(screen.getByTestId("chat-input")).toHaveValue(
      'texto existente\n{"ok":true}',
    );
  });
});

// @vitest-environment jsdom
/**
 * Abrir com Vectora: o que o app faz com arquivos e pastas entregues pelo sistema.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VectoraOpenedPath } from "@/lib/types/vectora-bridge";
import {
  openSystemFiles,
  sessionIdFromDeepLink,
  type OpenSystemFilesDeps,
} from "../open-system-files";

const processor = vi.hoisted(() => ({ processAttachmentFiles: vi.fn() }));
vi.mock("@/lib/utils/chat/process-files", () => processor);

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, key) => (params?: Record<string, string>) =>
        params
          ? `${String(key)}:${Object.values(params).join(",")}`
          : String(key),
    },
  ),
}));

const file = (name: string, mimeType = ""): VectoraOpenedPath => ({
  kind: "file",
  path: `/tmp/${name}`,
  name,
  mimeType,
  size: 3,
  data: new Uint8Array([1, 2, 3]),
});
const directory = (name: string): VectoraOpenedPath => ({
  kind: "directory",
  path: `/work/${name}`,
  name,
});
const failure = (
  name: string,
  reason: "missing" | "unreadable" | "too_large" | "unsupported",
): VectoraOpenedPath => ({ kind: "error", path: `/tmp/${name}`, name, reason });

let deps: OpenSystemFilesDeps;
beforeEach(() => {
  processor.processAttachmentFiles.mockReset();
  processor.processAttachmentFiles.mockImplementation(
    async (files: File[]) => ({
      attachments: files.map((f) => ({ id: f.name, name: f.name })),
      error: null,
    }),
  );
  deps = {
    createWorkspace: vi.fn().mockResolvedValue("ws-1"),
    addAttachments: vi.fn(),
    openNewSession: vi.fn(),
    notifyError: vi.fn(),
  };
});

describe("openSystemFiles", () => {
  it("abre uma conversa nova com os arquivos como anexos, na ordem recebida", async () => {
    await openSystemFiles(
      [file("a.pdf", "application/pdf"), file("b.png")],
      deps,
    );

    const sent = processor.processAttachmentFiles.mock.calls[0]![0] as File[];
    expect(sent.map((f) => f.name)).toEqual(["a.pdf", "b.png"]);
    expect(sent[0]!.type).toBe("application/pdf");
    expect(await sent[0]!.arrayBuffer()).toEqual(
      new Uint8Array([1, 2, 3]).buffer,
    );
    expect(deps.addAttachments).toHaveBeenCalledWith([
      { id: "a.pdf", name: "a.pdf" },
      { id: "b.png", name: "b.png" },
    ]);
    expect(deps.openNewSession).toHaveBeenCalledWith(null);
  });

  it("uma pasta vira workspace e a conversa nova abre nele, sem anexos", async () => {
    await openSystemFiles([directory("projeto")], deps);

    expect(deps.createWorkspace).toHaveBeenCalledWith("/work/projeto");
    expect(deps.addAttachments).not.toHaveBeenCalled();
    expect(deps.openNewSession).toHaveBeenCalledWith("ws-1");
  });

  it("pasta e arquivos juntos: a conversa abre no workspace com os anexos", async () => {
    await openSystemFiles([file("a.md"), directory("projeto")], deps);

    expect(deps.addAttachments).toHaveBeenCalledTimes(1);
    expect(deps.openNewSession).toHaveBeenCalledTimes(1);
    expect(deps.openNewSession).toHaveBeenCalledWith("ws-1");
  });

  it("várias pastas viram vários workspaces e vale o último criado", async () => {
    deps.createWorkspace = vi
      .fn()
      .mockResolvedValueOnce("ws-1")
      .mockResolvedValueOnce("ws-2");

    await openSystemFiles([directory("a"), directory("b")], deps);

    expect(deps.createWorkspace).toHaveBeenCalledTimes(2);
    expect(deps.openNewSession).toHaveBeenCalledWith("ws-2");
  });

  it("pasta que não pôde virar workspace não abre conversa", async () => {
    deps.createWorkspace = vi.fn().mockResolvedValue(null);

    await openSystemFiles([directory("proibida")], deps);

    expect(deps.openNewSession).not.toHaveBeenCalled();
  });

  it("cada falha de leitura vira um aviso com o motivo certo e não impede os demais", async () => {
    await openSystemFiles(
      [
        failure("a", "missing"),
        failure("b", "unreadable"),
        failure("c", "too_large"),
        failure("d", "unsupported"),
        file("ok.txt"),
      ],
      deps,
    );

    expect(
      vi.mocked(deps.notifyError).mock.calls.map((call) => call[0]),
    ).toEqual([
      "open_files_error_missing:a",
      "open_files_error_unreadable:b",
      "open_files_error_too_large:c",
      "open_files_error_unsupported:d",
    ]);
    expect(deps.addAttachments).toHaveBeenCalledTimes(1);
    expect(deps.openNewSession).toHaveBeenCalledWith(null);
  });

  it("arquivo recusado na validação avisa e os aceitos ainda são anexados", async () => {
    processor.processAttachmentFiles.mockResolvedValue({
      attachments: [{ id: "ok", name: "ok.txt" }],
      error: "File must be smaller than 10MB",
    });

    await openSystemFiles([file("ok.txt"), file("grande.png")], deps);

    expect(deps.notifyError).toHaveBeenCalledWith(
      "open_files_error_attachment:File must be smaller than 10MB",
    );
    expect(deps.addAttachments).toHaveBeenCalledTimes(1);
    expect(deps.openNewSession).toHaveBeenCalledTimes(1);
  });

  it("se nenhum arquivo é aceito, não abre conversa vazia", async () => {
    processor.processAttachmentFiles.mockResolvedValue({
      attachments: [],
      error: "File is empty",
    });

    await openSystemFiles([file("vazio.txt")], deps);

    expect(deps.addAttachments).not.toHaveBeenCalled();
    expect(deps.openNewSession).not.toHaveBeenCalled();
    expect(deps.notifyError).toHaveBeenCalledTimes(1);
  });

  it("sem itens, não faz nada", async () => {
    await openSystemFiles([], deps);

    expect(deps.openNewSession).not.toHaveBeenCalled();
    expect(processor.processAttachmentFiles).not.toHaveBeenCalled();
  });
});

describe("sessionIdFromDeepLink", () => {
  it.each([
    ["vectora://session/abc123", "abc123"],
    ["vectora://session/a-b_c/", "a-b_c"],
  ])("aceita %s", (url, id) => {
    expect(sessionIdFromDeepLink(url)).toBe(id);
  });

  it.each([
    "vectora://session/",
    "vectora://session/../etc/passwd",
    "vectora://session/a/b",
    "vectora://settings/x",
    "https://session/abc",
    `vectora://session/${"a".repeat(65)}`,
  ])("recusa %s", (url) => {
    expect(sessionIdFromDeepLink(url)).toBeNull();
  });
});

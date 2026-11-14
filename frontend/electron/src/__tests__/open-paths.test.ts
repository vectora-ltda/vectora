import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MAX_OPEN_FILE_BYTES,
  createOpenPathsDispatcher,
  mimeTypeOf,
  pathsFromArgv,
  readOpenedPath,
  type OpenedPath,
} from "../open-paths";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(path.join(os.tmpdir(), "vectora-open-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe("pathsFromArgv", () => {
  it("ignora o executável, as opções e os URLs de outros protocolos", () => {
    const argv = [
      "/usr/bin/vectora",
      "--no-sandbox",
      "vectora://session/abc",
      "https://example.com/x",
      "/home/u/nota.md",
    ];

    expect(pathsFromArgv(argv)).toEqual(["/home/u/nota.md"]);
  });

  it("em desenvolvimento também ignora o app que o Electron recebe como argumento", () => {
    const argv = ["electron", "/repo/frontend", "/home/u/foto.png"];

    expect(pathsFromArgv(argv, { defaultApp: true })).toEqual([
      "/home/u/foto.png",
    ]);
    expect(pathsFromArgv(argv)).toEqual(["/repo/frontend", "/home/u/foto.png"]);
  });

  it("converte file:// (o que o Linux entrega com %U), inclusive com espaço e acento", () => {
    const argv = ["vectora", "file:///home/u/Meus%20docs/relat%C3%B3rio.pdf"];

    expect(pathsFromArgv(argv)).toEqual(["/home/u/Meus docs/relatório.pdf"]);
  });

  it("resolve caminho relativo pelo diretório de quem chamou", () => {
    expect(pathsFromArgv(["vectora", "a/b.txt"], { cwd: "/work" })).toEqual([
      path.resolve("/work", "a/b.txt"),
    ]);
  });

  it("descarta repetidos e argumentos vazios", () => {
    const argv = ["vectora", "/a.txt", "", "/a.txt", "/b.txt"];

    expect(pathsFromArgv(argv)).toEqual(["/a.txt", "/b.txt"]);
  });

  it("um file:// inválido é ignorado, sem lançar", () => {
    expect(pathsFromArgv(["vectora", "file://%"])).toEqual([]);
  });
});

describe("readOpenedPath", () => {
  it("lê um arquivo com os bytes, o tipo e o tamanho", async () => {
    const file = path.join(dir, "nota.md");
    writeFileSync(file, "# oi");

    const result = await readOpenedPath(file);

    expect(result.kind).toBe("file");
    if (result.kind !== "file") return;
    expect(result.name).toBe("nota.md");
    expect(result.mimeType).toBe("text/markdown");
    expect(result.size).toBe(4);
    expect(Buffer.from(result.data).toString()).toBe("# oi");
  });

  it("devolve uma pasta como pasta, sem ler o conteúdo", async () => {
    const folder = path.join(dir, "projeto");
    mkdirSync(folder);

    expect(await readOpenedPath(folder)).toEqual({
      kind: "directory",
      path: folder,
      name: "projeto",
    });
  });

  it("caminho que não existe vira o erro missing", async () => {
    const result = await readOpenedPath(path.join(dir, "nao-existe.txt"));

    expect(result).toMatchObject({ kind: "error", reason: "missing" });
  });

  it("arquivo acima do teto vira too_large sem ser lido", async () => {
    const file = path.join(dir, "grande.bin");
    writeFileSync(file, Buffer.alloc(8));
    // O teto é de 50 MB; um arquivo esparso de 51 MB basta para ultrapassá-lo.
    const { truncateSync } = await import("node:fs");
    truncateSync(file, MAX_OPEN_FILE_BYTES + 1);

    expect(await readOpenedPath(file)).toMatchObject({
      kind: "error",
      reason: "too_large",
    });
  });

  it("arquivo sem permissão de leitura vira unreadable", async () => {
    if (process.platform === "win32" || process.getuid?.() === 0) return;
    const file = path.join(dir, "secreto.txt");
    writeFileSync(file, "x", { mode: 0o000 });

    expect(await readOpenedPath(file)).toMatchObject({
      kind: "error",
      reason: "unreadable",
    });
  });

  it("o que não é arquivo nem pasta vira unsupported", async () => {
    if (process.platform === "win32") return;
    expect(await readOpenedPath("/dev/null")).toMatchObject({
      kind: "error",
      reason: "unsupported",
    });
  });
});

describe("mimeTypeOf", () => {
  it("usa a extensão sem diferenciar maiúsculas e devolve vazio quando não conhece", () => {
    expect(mimeTypeOf("FOTO.PNG")).toBe("image/png");
    expect(mimeTypeOf("script.py")).toBe("");
    expect(mimeTypeOf("semextensao")).toBe("");
  });
});

const item = (name: string): OpenedPath => ({
  kind: "directory",
  path: `/w/${name}`,
  name,
});

const read = async (filePath: string): Promise<OpenedPath> =>
  item(path.basename(filePath));

function setup(canReceive = true) {
  const sent: OpenedPath[][] = [];
  const focus = vi.fn();
  const sink = {
    canReceive: () => canReceive,
    send: (items: OpenedPath[]) => sent.push(items),
    focus,
  };
  return { sent, focus, dispatcher: createOpenPathsDispatcher(sink, read) };
}

describe("createOpenPathsDispatcher", () => {
  it("guarda o que chega antes de a janela pedir e o entrega uma única vez", async () => {
    const { dispatcher, sent } = setup();

    dispatcher.enqueue(["/w/a", "/w/b"]);
    dispatcher.enqueue(["/w/c"]);

    expect((await dispatcher.take()).map((entry) => entry.name)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(await dispatcher.take()).toEqual([]);
    expect(sent).toEqual([]);
  });

  it("depois de a janela pedir, o que chega vai direto a ela e a traz para a frente", async () => {
    const { dispatcher, sent, focus } = setup();
    await dispatcher.take();

    dispatcher.enqueue(["/w/novo"]);
    await vi.waitFor(() => expect(sent).toHaveLength(1));

    expect(sent[0]!.map((entry) => entry.name)).toEqual(["novo"]);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("se a janela ainda está carregando, o que chega fica guardado para o próximo pedido", async () => {
    const { dispatcher, sent } = setup(false);
    await dispatcher.take();

    dispatcher.enqueue(["/w/durante"]);

    expect(sent).toEqual([]);
    expect((await dispatcher.take()).map((entry) => entry.name)).toEqual([
      "durante",
    ]);
  });

  it("enfileirar uma lista vazia não faz nada", async () => {
    const { dispatcher, sent } = setup();
    await dispatcher.take();

    dispatcher.enqueue([]);

    expect(sent).toEqual([]);
    expect(await dispatcher.take()).toEqual([]);
  });
});

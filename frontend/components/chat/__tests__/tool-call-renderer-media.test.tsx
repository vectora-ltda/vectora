// @vitest-environment jsdom
/**
 * Entrega de mídia gerada pelo assistente no chat.
 *
 * `generate_image` declarava `render_hint="image"` — string que não bate
 * com nenhuma chave de `RENDERERS` (só `"image_preview"`/
 * `"browser_screenshot"` disparam `ImagePreview`), então a imagem gerada
 * sempre caía no fallback de JSON cru, mesmo depois do backend passar a
 * devolver um `url` servível (`media.py::_media_url`). `text_to_speech`/
 * `generate_video` ganharam um link de download real em `ArtifactCard`
 * (antes só mostrava o `path` de arquivo do servidor, texto puro e
 * inútil no browser).
 */

import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ToolCallRenderer } from "../tool-call-renderer";
import type { ToolCall } from "@/lib/types";

afterEach(cleanup);

function makeTool(over: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "tc-1",
    name: "generate_image",
    args: { prompt: "um gato" },
    renderHint: "image_preview",
    ...over,
  };
}

describe("ToolCallRenderer — mídia gerada pelo assistente", () => {
  it("generate_image com render_hint=image_preview renderiza a imagem inline", () => {
    const tool = makeTool({
      output: JSON.stringify({
        url: "/artifacts/t1/media/gato.png",
        path: "C:\\Users\\x\\.vectora\\artifacts\\t1\\media\\gato.png",
        provider: "openai",
        bytes: 1234,
      }),
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );

    // `alt=""` (sem descrição no payload) faz o browser tratar como
    // decorativa (role="presentation", não "img") — query por tag em vez
    // de role, o mesmo caminho de acessibilidade real que a UI usa hoje.
    const img = container.querySelector("img") as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.src).toContain("/artifacts/t1/media/gato.png");
  });

  it("render_hint desconhecido (regressão do valor antigo 'image') nunca mostra a imagem — cai no JSON cru", () => {
    const tool = makeTool({
      renderHint: "image" as ToolCall["renderHint"],
      output: JSON.stringify({ url: "/artifacts/t1/media/gato.png" }),
    });
    const { container } = render(
      <ToolCallRenderer tool={tool} isStreaming={false} />,
    );

    expect(container.querySelector("img")).toBeNull();
  });

  it("text_to_speech/generate_video com url mostram link de download, não o path cru", () => {
    const tool = makeTool({
      name: "text_to_speech",
      renderHint: "artifact",
      output: JSON.stringify({
        title: "20260826-120000-abcdef12.mp3",
        artifact_type: "audio",
        url: "/artifacts/t1/media/voz.mp3",
        path: "C:\\Users\\x\\.vectora\\artifacts\\t1\\media\\voz.mp3",
      }),
    });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);

    const link = screen.getByRole("link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/artifacts/t1/media/voz.mp3");
    // O path de servidor não pode aparecer como texto pro usuário quando
    // já existe uma URL servível — era exatamente isso que a UI mostrava
    // antes (inútil, o usuário não tem acesso ao filesystem do backend).
    expect(screen.queryByText(/\.vectora\\artifacts/)).toBeNull();
  });

  it("artifact de markdown (create_artifact, sem url) continua mostrando o path — comportamento preservado", () => {
    const tool = makeTool({
      name: "create_artifact",
      renderHint: "artifact",
      output: JSON.stringify({
        title: "Plano de sprint",
        path: "/home/user/.vectora/artifacts/t1/plano.md",
        artifact_type: "plan",
      }),
    });
    render(<ToolCallRenderer tool={tool} isStreaming={false} />);

    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.getByText("/home/user/.vectora/artifacts/t1/plano.md"),
    ).toBeDefined();
  });
});

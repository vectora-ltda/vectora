// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ImageAttachment } from "@/lib/types";
import { useFileUpload } from "../use-file-upload";

const file = (id: string): ImageAttachment => ({
  id,
  mimeType: "image/png",
  name: `${id}.png`,
});

describe("useFileUpload — restauração de anexos", () => {
  it("começa com os anexos iniciais (rascunho restaurado ao remontar)", () => {
    const { result } = renderHook(() =>
      useFileUpload({ initialFiles: [file("a"), file("b")] }),
    );
    expect(result.current.attachedFiles.map((f) => f.id)).toEqual(["a", "b"]);
  });

  it("replaceFiles troca a lista inteira e limpa o erro de upload", () => {
    const { result } = renderHook(() =>
      useFileUpload({ initialFiles: [file("a")] }),
    );
    act(() => result.current.setUploadError("falhou"));
    act(() => result.current.replaceFiles([file("c")]));
    expect(result.current.attachedFiles.map((f) => f.id)).toEqual(["c"]);
    expect(result.current.uploadError).toBeNull();
  });

  it("sem opções começa vazio", () => {
    const { result } = renderHook(() => useFileUpload());
    expect(result.current.attachedFiles).toEqual([]);
  });
});

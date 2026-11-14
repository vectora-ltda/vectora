import { beforeEach, describe, expect, it } from "vitest";
import type { ImageAttachment } from "@/lib/types";
import { useOpenedFilesStore } from "../opened-files-store";

const attachment = (id: string) => ({ id, name: id }) as ImageAttachment;

beforeEach(() => useOpenedFilesStore.setState({ pending: [] }));

describe("opened-files-store", () => {
  it("acumula os anexos na ordem em que chegam", () => {
    useOpenedFilesStore.getState().add([attachment("a")]);
    useOpenedFilesStore.getState().add([attachment("b"), attachment("c")]);

    expect(useOpenedFilesStore.getState().pending.map((a) => a.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("take entrega tudo uma única vez e esvazia", () => {
    useOpenedFilesStore.getState().add([attachment("a")]);

    expect(
      useOpenedFilesStore
        .getState()
        .take()
        .map((a) => a.id),
    ).toEqual(["a"]);
    expect(useOpenedFilesStore.getState().take()).toEqual([]);
    expect(useOpenedFilesStore.getState().pending).toEqual([]);
  });
});

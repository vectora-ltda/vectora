// @vitest-environment jsdom
/**
 * context-graph-settings-store: tipos de arquivo (default = todos) + modo,
 * persistidos. toggleFileType adiciona/remove; setMode troca o modo.
 */

import { describe, it, expect, beforeEach } from "vitest";

import {
  useContextGraphSettingsStore,
  ALL_GRAPH_FILE_TYPES,
} from "@/lib/stores/context-graph-settings-store";

beforeEach(() => {
  if (typeof localStorage !== "undefined") localStorage.clear();
  useContextGraphSettingsStore.setState({
    fileTypes: [...ALL_GRAPH_FILE_TYPES],
    mode: "semantic",
  });
});

const s = () => useContextGraphSettingsStore.getState();

describe("context-graph-settings-store", () => {
  it("default indexa todos os tipos e modo semantic", () => {
    expect(s().fileTypes).toEqual(["code", "document", "paper"]);
    expect(s().mode).toBe("semantic");
  });

  it("toggleFileType remove um tipo marcado", () => {
    s().toggleFileType("code");
    expect(s().fileTypes).toEqual(["document", "paper"]);
  });

  it("toggleFileType re-adiciona um tipo desmarcado", () => {
    s().toggleFileType("code");
    s().toggleFileType("code");
    expect(s().fileTypes).toContain("code");
  });

  it("permite restringir a só documents (caso Obsidian)", () => {
    s().toggleFileType("code");
    s().toggleFileType("paper");
    expect(s().fileTypes).toEqual(["document"]);
  });

  it("setMode troca para ast", () => {
    s().setMode("ast");
    expect(s().mode).toBe("ast");
  });
});

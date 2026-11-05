// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { mountSandboxedExtension } from "../../../vext-sdk/packages/sdk/src/sandbox";
import type { VextManifest } from "../../../vext-sdk/packages/sdk/src/index";

const manifest: VextManifest = {
  id: "example.extension",
  publisher: "test",
  name: "Example",
  version: "1.0.0",
  api_version: 1,
  protocol_version: 1,
  runtime: "none",
  entrypoint: "main.js",
  permissions: [],
  platforms: ["any"],
  contributions: {},
  files: [],
  integrity: null,
  provenance: {},
};

afterEach(() => document.body.replaceChildren());

describe("mountSandboxedExtension", () => {
  it("forwards trusted-frame requests and rejects spoofed sources", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const onRequest = vi.fn().mockResolvedValue({ ok: true });
    const mounted = mountSandboxedExtension(
      container,
      "https://example.test/extension.js",
      manifest,
      onRequest,
    );
    const request = {
      jsonrpc: "2.0" as const,
      id: "request-1",
      method: "workspace.read",
      params: { path: "README.md" },
    };

    window.dispatchEvent(
      new MessageEvent("message", { source: window, data: request }),
    );
    expect(onRequest).not.toHaveBeenCalled();
    mounted.frame.dispatchEvent(new Event("load"));
    window.dispatchEvent(
      new MessageEvent("message", {
        source: mounted.frame.contentWindow,
        data: request,
      }),
    );
    await vi.waitFor(() =>
      expect(onRequest).toHaveBeenCalledWith(request.method, request.params),
    );
    mounted.dispose();
    expect(container).not.toContainElement(mounted.frame);
  });
});

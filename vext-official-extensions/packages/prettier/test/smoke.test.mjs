import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("manifest declares full-stack entrypoints", async () => {
  const manifest = JSON.parse(await readFile(new URL("../vectora-extension.json", import.meta.url)));
  assert.ok(manifest.frontend && manifest.backend);
  assert.ok(Array.isArray(manifest.permissions));
});

test("backend validates formatting requests before invoking the tool", async () => {
  const { handle } = await import("../dist/backend.js");
  await assert.rejects(() => handle({ method: "format" }), /text is required/);
});

test("backend bounds accumulated output by UTF-8 bytes", async () => {
  const { appendBoundedOutput } = await import("../dist/backend.js");
  const first = appendBoundedOutput("é", "é", 3);
  assert.equal(first.exceeded, true);
  assert.equal(Buffer.byteLength(first.value), 2);
});


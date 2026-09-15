import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("Docker extension declares both entrypoints", async () => {
  const manifest = JSON.parse(await readFile(new URL("../vectora-extension.json", import.meta.url)));
  assert.equal(typeof manifest.frontend, "string");
  assert.equal(typeof manifest.backend, "string");
});

test("Docker backend rejects unsupported methods", async () => {
  const { handle } = await import("../dist/backend.js");
  await assert.rejects(() => handle({ method: "exec" }), /Unsupported Docker method/);
});


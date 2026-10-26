import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("manifest declares full-stack entrypoints", async () => {
  const manifest = JSON.parse(await readFile(new URL("../vectora-extension.json", import.meta.url)));
  assert.ok(manifest.frontend && manifest.backend);
  assert.ok(Array.isArray(manifest.permissions));
});

test("backend bounds output and exposes the read-only process contract", async () => {
  const manifest = JSON.parse(await readFile(new URL("../vectora-extension.json", import.meta.url)));
  assert.ok(manifest.permissions.includes("process.spawn"));
  const { appendBoundedOutput } = await import("../dist/backend.js");
  const bounded = appendBoundedOutput("é", "é", 3);
  assert.equal(bounded.exceeded, true);
  assert.equal(Buffer.byteLength(bounded.value), 2);
});


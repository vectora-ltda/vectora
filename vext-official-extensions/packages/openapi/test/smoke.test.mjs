import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("manifest declares full-stack entrypoints", async () => {
  const manifest = JSON.parse(await readFile(new URL("../vectora-extension.json", import.meta.url)));
  assert.ok(manifest.frontend && manifest.backend);
  assert.ok(Array.isArray(manifest.permissions));
});


import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { handle } from "../dist/backend.js";

async function workspaceWith(config) {
  const root = await mkdtemp(join(tmpdir(), "devc-"));
  await mkdir(join(root, ".devcontainer"));
  await writeFile(join(root, ".devcontainer", "devcontainer.json"), config);
  return root;
}

test("reads devcontainer.json inside the workspace and ignores line comments", async () => {
  const root = await workspaceWith('{\n  // name\n  "name": "demo"\n}');
  try {
    const { items } = await handle({ method: "inspect", params: { workspace: root } });
    assert.equal(items.length, 1);
    assert.equal(items[0].configuration.name, "demo");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects a missing, empty or relative workspace", async () => {
  await assert.rejects(handle({ method: "inspect" }), /workspace is required/);
  await assert.rejects(handle({ method: "inspect", params: { workspace: "" } }), /workspace is required/);
  await assert.rejects(
    handle({ method: "inspect", params: { workspace: "relative/dir" } }),
    /absolute path/,
  );
});

test("rejects a workspace path containing a NUL byte", async () => {
  await assert.rejects(
    handle({ method: "inspect", params: { workspace: join(tmpdir(), "a\0b") } }),
    /absolute path/,
  );
});

test("fails when the workspace has no devcontainer configuration", async () => {
  const root = await mkdtemp(join(tmpdir(), "devc-empty-"));
  try {
    await assert.rejects(
      handle({ method: "inspect", params: { workspace: root } }),
      /not found or invalid/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("does not follow a .devcontainer link that points outside the workspace", async () => {
  const outside = await workspaceWith('{ "name": "secret" }');
  const root = await mkdtemp(join(tmpdir(), "devc-link-"));
  try {
    await symlink(join(outside, ".devcontainer"), join(root, ".devcontainer"), "junction");
    await assert.rejects(
      handle({ method: "inspect", params: { workspace: root } }),
      /not found or invalid/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  }
});

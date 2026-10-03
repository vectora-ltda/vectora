import { afterEach, describe, expect, it, vi } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { BrowserCredentialStore } from "../browser-credentials";

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0))
    await fs.rm(root, { recursive: true, force: true });
});
async function setup() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "vectora-vault-"));
  roots.push(root);
  const encryption = {
    isEncryptionAvailable: vi.fn(() => true),
    encryptString: (text: string) => Buffer.from(text),
    decryptString: (buffer: Buffer) => buffer.toString(),
  };
  return {
    root,
    encryption,
    store: new BrowserCredentialStore(root, encryption),
  };
}
const input = {
  origin: "https://example.com",
  username: "user",
  password: "test-password",
};
describe("credential transactions", () => {
  it("serializes overlapping saves and deletes without resurrecting records", async () => {
    const { store, root } = await setup();
    const original = await store.save("default", input);
    await Promise.all([
      store.save("default", { ...input, username: "second" }),
      store.delete("default", original.id),
    ]);
    expect(
      (await store.list("default")).map((record) => record.username),
    ).toEqual(["second"]);
    expect(await fs.readdir(root)).toEqual(["default.dat"]);
  });
  it("serializes clear after pending saves and clears without encryption", async () => {
    const { store, encryption } = await setup();
    await Promise.all([store.save("default", input), store.clear("default")]);
    expect(await store.list("default")).toEqual([]);
    await store.save("default", input);
    encryption.isEncryptionAvailable.mockReturnValue(false);
    await store.clear("default");
    expect(await store.list("default")).toEqual([]);
  });
  it("preserves unreadable vaults and recovers the queue after an error", async () => {
    const { store, root } = await setup();
    await fs.writeFile(path.join(root, "default.dat"), "corrupt");
    await expect(store.save("default", input)).rejects.toThrow();
    expect(await fs.readFile(path.join(root, "default.dat"), "utf8")).toBe(
      "corrupt",
    );
    await store.clear("default");
    await store.save("default", input);
    expect(await store.list("default")).toHaveLength(1);
  });
  it("rejects traversal before touching the filesystem", async () => {
    const { store } = await setup();
    expect(() => store.list("../other")).toThrow();
    expect(() => store.clear("session-../../other")).toThrow();
  });
});

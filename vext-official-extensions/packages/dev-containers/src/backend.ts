import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

export type Request = { method: "inspect"; params?: { workspace?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const workspace = request.params?.workspace;
  if (!workspace) throw new Error("workspace is required");
  const root = resolve(workspace);
  const candidates = [join(root, ".devcontainer", "devcontainer.json"), join(root, ".devcontainer.json")];
  for (const path of candidates) {
    try {
      const text = await readFile(path, "utf8");
      return { items: [{ path, configuration: JSON.parse(text.replace(/\/\/.*$/gm, "")) }] };
    } catch { /* try next candidate */ }
  }
  throw new Error("devcontainer.json not found or invalid");
}

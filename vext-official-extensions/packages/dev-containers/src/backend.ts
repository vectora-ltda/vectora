import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, resolve, sep } from "node:path";

export type Request = { method: "inspect"; params?: { workspace?: string } };

// A configuração só é lida de dentro do workspace informado: o caminho real do
// arquivo (depois de resolver links) precisa ficar sob a raiz real do workspace.
function isInside(root: string, path: string): boolean {
  return path === root || path.startsWith(root + sep);
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const workspace = request.params?.workspace;
  if (!workspace) throw new Error("workspace is required");
  if (workspace.includes("\0") || !isAbsolute(workspace)) {
    throw new Error("workspace must be an absolute path");
  }
  let root: string;
  try {
    root = await realpath(resolve(workspace));
  } catch {
    throw new Error("workspace not found");
  }
  const candidates = [
    join(root, ".devcontainer", "devcontainer.json"),
    join(root, ".devcontainer.json"),
  ];
  for (const candidate of candidates) {
    try {
      const real = await realpath(candidate);
      if (!isInside(root, real)) continue;
      const text = await readFile(real, "utf8");
      return {
        items: [
          {
            path: candidate,
            configuration: JSON.parse(text.replace(/\/\/.*$/gm, "")),
          },
        ],
      };
    } catch {
      /* try next candidate */
    }
  }
  throw new Error("devcontainer.json not found or invalid");
}

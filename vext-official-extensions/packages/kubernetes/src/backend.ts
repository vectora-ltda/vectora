import { spawn } from "node:child_process";

export type Request = { method: "get" | "contexts"; params?: { resource?: string; namespace?: string; context?: string } };

function run(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("kubectl", args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = ""; let err = "";
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error("kubectl timed out")); }, 15_000);
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { out += chunk; if (out.length > 2_000_000) child.kill("SIGKILL"); });
    child.stderr.on("data", (chunk: string) => { err += chunk.slice(0, 2_000_000); });
    child.once("error", (cause) => { clearTimeout(timer); reject(cause); });
    child.once("close", (code) => { clearTimeout(timer); if (code !== 0) reject(new Error(err.trim() || `kubectl exited with code ${code ?? "unknown"}`)); else resolve(out); });
  });
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  if (request.method === "contexts") return { items: (await run(["config", "get-contexts", "-o", "name"])).split(/\r?\n/).filter(Boolean) };
  const resource = request.params?.resource;
  if (!resource || !/^[a-z][a-z0-9.-]{0,62}$/.test(resource)) throw new Error("valid Kubernetes resource is required");
  const args = ["get", resource, "-o", "json"];
  if (request.params?.namespace) args.push("-n", request.params.namespace);
  if (request.params?.context) args.push("--context", request.params.context);
  const output = await run(args);
  return { items: [JSON.parse(output)] };
}

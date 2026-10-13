import { spawn } from "node:child_process";

export type Request = { method: "run"; params?: { cwd?: string; project?: string; grep?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const cwd = request.params?.cwd ?? process.cwd();
  const args = ["test", "--reporter=json"];
  if (request.params?.project) args.push("--project", request.params.project);
  if (request.params?.grep) args.push("--grep", request.params.grep);
  return new Promise((resolve, reject) => {
    const child = spawn("npx", ["playwright", ...args], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let output = ""; let error = "";
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error("Playwright timed out")); }, 60_000);
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { output += chunk; if (output.length > 4_000_000) child.kill("SIGKILL"); });
    child.stderr.on("data", (chunk: string) => { error += chunk.slice(0, 1_000_000); });
    child.once("error", (cause) => { clearTimeout(timer); reject(cause); });
    child.once("close", (code) => { clearTimeout(timer); if (code !== 0 && !output) reject(new Error(error.trim() || `Playwright exited with code ${code ?? "unknown"}`)); else { try { resolve({ items: [JSON.parse(output)] }); } catch { resolve({ items: [{ output, exitCode: code }] }); } } });
  });
}

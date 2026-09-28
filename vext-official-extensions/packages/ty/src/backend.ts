import { spawn } from "node:child_process";

export type Request = { method: "check" | "version"; params?: { cwd?: string; files?: string[] } };

function run(args: string[], cwd = process.cwd()): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("ty", args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = ""; let stderr = "";
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error("Ty timed out")); }, 15_000);
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { stdout += chunk; if (stdout.length > 2_000_000) child.kill("SIGKILL"); });
    child.stderr.on("data", (chunk: string) => { stderr += chunk.slice(0, 2_000_000); });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("close", (code) => { clearTimeout(timer); if (code !== 0 && !stdout && args[0] !== "check") reject(new Error(stderr.trim() || `Ty exited with code ${code ?? "unknown"}`)); else resolve(stdout || stderr); });
  });
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const cwd = request.params?.cwd;
  if (request.method === "version") return { items: [{ version: (await run(["--version"], cwd)).trim() }] };
  const output = await run(["check", ...(request.params?.files ?? ["."])], cwd);
  return { items: [{ output }] };
}

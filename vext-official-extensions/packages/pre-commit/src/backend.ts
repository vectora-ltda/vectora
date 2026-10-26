import { spawn } from "node:child_process";

export type Request = {
  method: "run" | "version";
  params?: { cwd?: string; hook?: string; files?: string[]; all?: boolean };
};

const MAX_OUTPUT_BYTES = 2_000_000;

function appendBounded(
  current: string,
  chunk: string,
): { value: string; exceeded: boolean } {
  const combined = Buffer.concat([Buffer.from(current), Buffer.from(chunk)]);
  let value = combined.subarray(0, MAX_OUTPUT_BYTES).toString("utf8");
  while (Buffer.byteLength(value) > MAX_OUTPUT_BYTES)
    value = value.slice(0, -1);
  return { value, exceeded: combined.byteLength > MAX_OUTPUT_BYTES };
}

function run(args: string[], cwd = process.cwd()): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("pre-commit", args, {
      cwd,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("pre-commit timed out"));
    }, 30_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      const bounded = appendBounded(stdout, chunk);
      stdout = bounded.value;
      if (bounded.exceeded) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      stderr = appendBounded(stderr, chunk).value;
    });
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0 && args[0] === "--version")
        reject(
          new Error(
            stderr.trim() || `pre-commit exited with code ${code ?? "unknown"}`,
          ),
        );
      else resolve(stdout || stderr);
    });
  });
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const cwd = request.params?.cwd;
  if (request.method === "version")
    return { items: [{ version: (await run(["--version"], cwd)).trim() }] };
  const args = ["run"];
  if (request.params?.all) args.push("--all-files");
  else if (request.params?.hook) args.push(request.params.hook);
  else {
    const files = request.params?.files ?? [];
    if (!files.length) throw new Error("hook, files or all is required");
    args.push("--files", ...files);
  }
  const output = await run(args, cwd);
  return { items: [{ output }] };
}

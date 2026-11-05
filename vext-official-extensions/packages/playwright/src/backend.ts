import { spawn } from "node:child_process";

export type Request = {
  method: "run";
  params?: { cwd?: string; project?: string; grep?: string };
};

const MAX_OUTPUT_BYTES = 4_000_000;
const MAX_ERROR_BYTES = 1_000_000;

function appendBounded(
  current: string,
  chunk: string,
  limit: number,
): { value: string; exceeded: boolean } {
  const combined = Buffer.concat([Buffer.from(current), Buffer.from(chunk)]);
  let value = combined.subarray(0, limit).toString("utf8");
  while (Buffer.byteLength(value) > limit) value = value.slice(0, -1);
  return { value, exceeded: combined.byteLength > limit };
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const cwd = request.params?.cwd ?? process.cwd();
  const args = ["test", "--reporter=json"];
  if (request.params?.project) args.push("--project", request.params.project);
  if (request.params?.grep) args.push("--grep", request.params.grep);
  return new Promise((resolve, reject) => {
    const child = spawn("npx", ["playwright", ...args], {
      cwd,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    let error = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("Playwright timed out"));
    }, 60_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      const bounded = appendBounded(output, chunk, MAX_OUTPUT_BYTES);
      output = bounded.value;
      if (bounded.exceeded) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      error = appendBounded(error, chunk, MAX_ERROR_BYTES).value;
    });
    child.once("error", (cause) => {
      clearTimeout(timer);
      reject(cause);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0 && !output)
        reject(
          new Error(
            error.trim() || `Playwright exited with code ${code ?? "unknown"}`,
          ),
        );
      else {
        try {
          resolve({ items: [JSON.parse(output)] });
        } catch {
          resolve({ items: [{ output, exitCode: code }] });
        }
      }
    });
  });
}

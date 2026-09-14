import { spawn } from "node:child_process";

export type Request = {
  method: "format" | "check";
  params?: { text?: string; parser?: string };
};

const MAX_OUTPUT = 2 * 1024 * 1024;

function runPrettier(args: string[], input: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("prettier", args, { stdio: ["pipe", "pipe", "pipe"] });
    let output = "";
    let error = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("Prettier command timed out"));
    }, 10_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output += chunk;
      if (output.length > MAX_OUTPUT) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      error += chunk.slice(0, MAX_OUTPUT);
    });
    child.once("error", (cause) => {
      clearTimeout(timer);
      reject(cause);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(error.trim() || `Prettier exited with code ${code ?? "unknown"}`));
        return;
      }
      resolve(output);
    });
    child.stdin.end(input);
  });
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  if (!request.method) throw new Error("method is required");
  const text = request.params?.text;
  if (typeof text !== "string") throw new Error("text is required");
  const parser = request.params?.parser ?? "babel";
  if (!/^[a-z][a-z0-9-]{1,31}$/.test(parser)) throw new Error("invalid parser");
  const formatted = await runPrettier(["--parser", parser, "--stdin-filepath", `input.${parser}`], text);
  return request.method === "check"
    ? { items: [{ formatted, changed: formatted !== text }] }
    : { items: [{ text: formatted }] };
}

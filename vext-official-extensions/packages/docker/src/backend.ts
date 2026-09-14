import { spawn } from "node:child_process";

export type DockerRequest = {
  method: "images" | "containers";
  params?: { all?: boolean };
};

const MAX_OUTPUT = 2 * 1024 * 1024;
const TIMEOUT_MS = 10_000;

function runDocker(args: string[]): Promise<unknown[]> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("Docker command timed out"));
    }, TIMEOUT_MS);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (stdout.length > MAX_OUTPUT) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk.slice(0, MAX_OUTPUT);
    });
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(stderr.trim() || `Docker exited with code ${code ?? "unknown"}`));
        return;
      }
      try {
        resolve(stdout.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line)));
      } catch {
        reject(new Error("Docker returned invalid JSON"));
      }
    });
  });
}

export async function handle(request: DockerRequest): Promise<{ items: unknown[] }> {
  if (request.method === "images") {
    return { items: await runDocker(["image", "ls", "--format", "{{json .}}", ...(request.params?.all ? ["--all"] : [])]) };
  }
  if (request.method === "containers") {
    return { items: await runDocker(["ps", "--format", "{{json .}}", ...(request.params?.all ? ["--all"] : [])]) };
  }
  throw new Error("Unsupported Docker method");
}

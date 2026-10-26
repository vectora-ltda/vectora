import { spawn } from "node:child_process";

export type Request = {
  method: "get" | "contexts";
  params?: { resource?: string; namespace?: string; context?: string };
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

function run(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("kubectl", args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("kubectl timed out"));
    }, 15_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      const bounded = appendBounded(out, chunk);
      out = bounded.value;
      if (bounded.exceeded) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      err = appendBounded(err, chunk).value;
    });
    child.once("error", (cause) => {
      clearTimeout(timer);
      reject(cause);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0)
        reject(
          new Error(
            err.trim() || `kubectl exited with code ${code ?? "unknown"}`,
          ),
        );
      else resolve(out);
    });
  });
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  if (request.method === "contexts")
    return {
      items: (await run(["config", "get-contexts", "-o", "name"]))
        .split(/\r?\n/)
        .filter(Boolean),
    };
  const resource = request.params?.resource;
  if (!resource || !/^[a-z][a-z0-9.-]{0,62}$/.test(resource))
    throw new Error("valid Kubernetes resource is required");
  const args = ["get", resource, "-o", "json"];
  if (request.params?.namespace) args.push("-n", request.params.namespace);
  if (request.params?.context) args.push("--context", request.params.context);
  const output = await run(args);
  return { items: [JSON.parse(output)] };
}

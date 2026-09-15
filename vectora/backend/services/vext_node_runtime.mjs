import fs from "node:fs/promises";
import { pathToFileURL } from "node:url";
import readline from "node:readline";

const args = process.argv.slice(2);
const rootIndex = args.indexOf("--root");
const entrypointIndex = args.indexOf("--entrypoint");
if (rootIndex < 0 || entrypointIndex < 0) {
  process.stderr.write("--root and --entrypoint are required\n");
  process.exitCode = 2;
} else {
  const root = args[rootIndex + 1];
  const entrypoint = args[entrypointIndex + 1];
  const modulePath = `${root}/${entrypoint}`.replaceAll("\\", "/");
  const extension = await import(pathToFileURL(modulePath).href);
  const handler = extension.handle ?? extension.default?.handle;
  if (typeof handler !== "function") {
    throw new Error("entrypoint must export handle(method, params)");
  }
  const input = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });
  for await (const line of input) {
    let requestId = null;
    try {
      if (Buffer.byteLength(line, "utf8") > 1024 * 1024) {
        throw new Error("request too large");
      }
      const request = JSON.parse(line);
      if (
        request === null ||
        typeof request !== "object" ||
        Array.isArray(request)
      ) {
        throw new Error("invalid JSON-RPC request");
      }
      if (
        request.jsonrpc !== "2.0" ||
        typeof request.method !== "string" ||
        !request.method
      ) {
        throw new Error("invalid JSON-RPC request");
      }
      if (
        request.params !== undefined &&
        (request.params === null ||
          typeof request.params !== "object" ||
          Array.isArray(request.params))
      ) {
        throw new Error("invalid JSON-RPC params");
      }
      if (
        request.id !== undefined &&
        request.id !== null &&
        typeof request.id !== "string" &&
        typeof request.id !== "number"
      ) {
        throw new Error("invalid JSON-RPC id");
      }
      requestId = request.id ?? null;
      const result = await handler(
        String(request.method ?? ""),
        request.params ?? {},
      );
      process.stdout.write(
        `${JSON.stringify({ jsonrpc: "2.0", id: requestId, result: result ?? null })}\n`,
      );
    } catch (error) {
      process.stdout.write(
        `${JSON.stringify({ jsonrpc: "2.0", id: requestId, error: String(error) })}\n`,
      );
    }
  }
}

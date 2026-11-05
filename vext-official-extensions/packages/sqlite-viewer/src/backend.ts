import { spawn } from "node:child_process";

export type Request = {
  method: "tables" | "query";
  params?: { database?: string; sql?: string };
};
const MAX_OUTPUT = 2_000_000;

export function appendBoundedOutput(
  current: string,
  chunk: string,
  limit = MAX_OUTPUT,
): { value: string; exceeded: boolean } {
  const combined = Buffer.concat([
    Buffer.from(current, "utf8"),
    Buffer.from(chunk, "utf8"),
  ]);
  let value = combined.subarray(0, limit).toString("utf8");
  while (Buffer.byteLength(value, "utf8") > limit) value = value.slice(0, -1);
  return { value, exceeded: combined.byteLength > limit };
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const database = request.params?.database;
  if (!database || database.length > 500 || /[\r\n]/.test(database))
    throw new Error("database path is required");
  const sql =
    request.method === "tables"
      ? "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
      : request.params?.sql;
  if (!sql || sql.length > 100_000 || !/^\s*(SELECT|PRAGMA)\b/i.test(sql))
    throw new Error("only SELECT or PRAGMA queries are allowed");
  return new Promise((resolve, reject) => {
    const child = spawn(
      "sqlite3",
      ["-json", "-cmd", "PRAGMA query_only=ON;", database, sql],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "";
    let error = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("SQLite query timed out"));
    }, 10_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      const bounded = appendBoundedOutput(output, chunk);
      output = bounded.value;
      if (bounded.exceeded) child.kill("SIGKILL");
    });
    child.stderr.on("data", (chunk: string) => {
      const bounded = appendBoundedOutput(error, chunk);
      error = bounded.value;
      if (bounded.exceeded) child.kill("SIGKILL");
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
            error.trim() || `sqlite3 exited with code ${code ?? "unknown"}`,
          ),
        );
      else {
        try {
          resolve({ items: JSON.parse(output || "[]") as unknown[] });
        } catch {
          reject(new Error("sqlite3 returned invalid JSON"));
        }
      }
    });
  });
}

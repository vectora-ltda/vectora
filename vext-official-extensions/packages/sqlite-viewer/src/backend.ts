import { spawn } from "node:child_process";

export type Request = { method: "tables" | "query"; params?: { database?: string; sql?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const database = request.params?.database;
  if (!database || database.length > 500 || /[\r\n]/.test(database)) throw new Error("database path is required");
  const sql = request.method === "tables" ? "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" : request.params?.sql;
  if (!sql || sql.length > 100_000 || !/^\s*(SELECT|PRAGMA)\b/i.test(sql)) throw new Error("only SELECT or PRAGMA queries are allowed");
  return new Promise((resolve, reject) => {
    const child = spawn("sqlite3", ["-json", database, sql], { stdio: ["ignore", "pipe", "pipe"] });
    let output = ""; let error = "";
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error("SQLite query timed out")); }, 10_000);
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { output += chunk; if (output.length > 2_000_000) child.kill("SIGKILL"); });
    child.stderr.on("data", (chunk: string) => { error += chunk.slice(0, 2_000_000); });
    child.once("error", (cause) => { clearTimeout(timer); reject(cause); });
    child.once("close", (code) => { clearTimeout(timer); if (code !== 0) reject(new Error(error.trim() || `sqlite3 exited with code ${code ?? "unknown"}`)); else { try { resolve({ items: JSON.parse(output || "[]") as unknown[] }); } catch { reject(new Error("sqlite3 returned invalid JSON")); } } });
  });
}

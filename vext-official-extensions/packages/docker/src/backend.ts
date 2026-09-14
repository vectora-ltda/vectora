export type DockerRequest = { method: "images" | "containers"; params?: Record<string, unknown> };

export async function handle(request: DockerRequest): Promise<{ items: unknown[] }> {
  if (request.method !== "images" && request.method !== "containers") {
    throw new Error("Unsupported Docker method");
  }
  return { items: [] };
}

export type GitHubRequest = { method: "issues" | "pulls"; params?: { owner?: string; repo?: string } };

export async function handle(request: GitHubRequest): Promise<{ items: unknown[] }> {
  if (request.method !== "issues" && request.method !== "pulls") {
    throw new Error("Unsupported GitHub method");
  }
  return { items: [] };
}

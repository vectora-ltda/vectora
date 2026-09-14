export type Request = { method: string; params?: Record<string, unknown> };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  if (!request.method) throw new Error("method is required");
  return { items: [] };
}

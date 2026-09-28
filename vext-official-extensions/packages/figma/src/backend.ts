export type Request = { method: "file"; params?: { fileKey?: string; token?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const fileKey = request.params?.fileKey;
  const token = request.params?.token ?? process.env.FIGMA_TOKEN;
  if (!fileKey || !/^[A-Za-z0-9]{10,200}$/.test(fileKey)) throw new Error("valid Figma fileKey is required");
  if (!token) throw new Error("Figma token is required");
  const response = await fetch(`https://api.figma.com/v1/files/${encodeURIComponent(fileKey)}`, { headers: { "X-Figma-Token": token }, signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Figma API returned ${response.status}`);
  const data: unknown = await response.json();
  return { items: [data] };
}

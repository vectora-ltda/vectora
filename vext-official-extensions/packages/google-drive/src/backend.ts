export type Request = { method: "list" | "get"; params?: { query?: string; fileId?: string; token?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const token = request.params?.token ?? process.env.GOOGLE_ACCESS_TOKEN;
  if (!token) throw new Error("Google access token is required");
  const id = request.params?.fileId;
  if (request.method === "get" && (!id || !/^[A-Za-z0-9_-]{5,200}$/.test(id))) throw new Error("valid fileId is required");
  const endpoint = request.method === "list"
    ? `https://www.googleapis.com/drive/v3/files?q=${encodeURIComponent(request.params?.query ?? "trashed = false")}&pageSize=100&fields=files(id,name,mimeType,modifiedTime,size)`
    : `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(id!) }?fields=id,name,mimeType,modifiedTime,size,webViewLink`;
  const response = await fetch(endpoint, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Google Drive API returned ${response.status}`);
  const data: unknown = await response.json();
  return { items: request.method === "list" && data && typeof data === "object" && "files" in data ? (data as { files: unknown[] }).files : [data] };
}

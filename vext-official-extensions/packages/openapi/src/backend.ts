export type Request = {
  method: "inspect";
  params?: { url?: string };
};

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  if (!url || !/^https:\/\//i.test(url)) throw new Error("valid HTTPS spec URL is required");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`OpenAPI spec returned ${response.status}`);
  const text = (await response.text()).slice(0, 2_000_000);
  let spec: unknown;
  try { spec = JSON.parse(text); } catch { throw new Error("OpenAPI spec must be JSON"); }
  if (!spec || typeof spec !== "object" || !("paths" in spec)) throw new Error("invalid OpenAPI document");
  const paths = (spec as { paths: unknown }).paths;
  if (!paths || typeof paths !== "object") throw new Error("invalid OpenAPI paths");
  return { items: Object.entries(paths as Record<string, unknown>).map(([path, operations]) => ({ path, operations: Object.keys((operations as object) ?? {}) })) };
}

export type Request = {
  method: "inspect";
  params?: { url?: string };
};

const MAX_RESPONSE_BYTES = 2_000_000;

async function readBoundedText(response: Response): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES)
    throw new Error("OpenAPI response exceeds the size limit");
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES)
        throw new Error("OpenAPI response exceeds the size limit");
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  if (!url || !/^https:\/\//i.test(url))
    throw new Error("valid HTTPS spec URL is required");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`OpenAPI spec returned ${response.status}`);
  const text = await readBoundedText(response);
  let spec: unknown;
  try {
    spec = JSON.parse(text);
  } catch {
    throw new Error("OpenAPI spec must be JSON");
  }
  if (!spec || typeof spec !== "object" || !("paths" in spec))
    throw new Error("invalid OpenAPI document");
  const paths = (spec as { paths: unknown }).paths;
  if (!paths || typeof paths !== "object")
    throw new Error("invalid OpenAPI paths");
  return {
    items: Object.entries(paths as Record<string, unknown>).map(
      ([path, operations]) => ({
        path,
        operations: Object.keys((operations as object) ?? {}),
      }),
    ),
  };
}

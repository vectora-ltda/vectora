export type Request = {
  method: "query";
  params?: {
    url?: string;
    query?: string;
    variables?: Record<string, unknown>;
    headers?: Record<string, string>;
  };
};

const MAX_RESPONSE_BYTES = 2_000_000;

async function readBoundedText(response: Response): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES)
    throw new Error("GraphQL response exceeds the size limit");
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
        throw new Error("GraphQL response exceeds the size limit");
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  const query = request.params?.query;
  if (!url || !/^https:\/\//i.test(url))
    throw new Error("valid HTTPS endpoint is required");
  if (!query || query.length > 1_000_000) throw new Error("query is required");
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...request.params?.headers },
    body: JSON.stringify({ query, variables: request.params?.variables ?? {} }),
    signal: AbortSignal.timeout(15_000),
  });
  const text = await readBoundedText(response);
  if (!response.ok)
    throw new Error(`GraphQL endpoint returned ${response.status}`);
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("GraphQL endpoint returned invalid JSON");
  }
  return { items: [payload] };
}

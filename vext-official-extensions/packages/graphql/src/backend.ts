export type Request = {
  method: "query";
  params?: {
    url?: string;
    query?: string;
    variables?: Record<string, unknown>;
    headers?: Record<string, string>;
  };
};

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  const query = request.params?.query;
  if (!url || !/^https:\/\//i.test(url)) throw new Error("valid HTTPS endpoint is required");
  if (!query || query.length > 1_000_000) throw new Error("query is required");
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...request.params?.headers },
    body: JSON.stringify({ query, variables: request.params?.variables ?? {} }),
    signal: AbortSignal.timeout(15_000),
  });
  const text = (await response.text()).slice(0, 2_000_000);
  if (!response.ok) throw new Error(`GraphQL endpoint returned ${response.status}`);
  let payload: unknown;
  try { payload = JSON.parse(text); } catch { throw new Error("GraphQL endpoint returned invalid JSON"); }
  return { items: [payload] };
}

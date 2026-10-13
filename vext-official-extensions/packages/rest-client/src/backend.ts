export type Request = {
  method: "request";
  params?: {
    url?: string;
    httpMethod?: string;
    headers?: Record<string, string>;
    body?: string;
  };
};

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  if (!url || !/^https?:\/\//i.test(url)) throw new Error("valid http(s) url is required");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(url, {
      method: request.params?.httpMethod ?? "GET",
      headers: request.params?.headers,
      body: request.params?.body,
      signal: controller.signal,
    });
    const text = (await response.text()).slice(0, 2_000_000);
    let body: unknown = text;
    try { body = JSON.parse(text); } catch { /* plain text response */ }
    return { items: [{ status: response.status, headers: Object.fromEntries(response.headers), body }] };
  } finally {
    clearTimeout(timer);
  }
}

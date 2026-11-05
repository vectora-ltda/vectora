export type Request = {
  method: "request";
  params?: {
    url?: string;
    httpMethod?: string;
    headers?: Record<string, string>;
    body?: string;
  };
};

const MAX_RESPONSE_BYTES = 2_000_000;

async function readBoundedText(response: Response): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES)
    throw new Error("HTTP response exceeds the size limit");
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
        throw new Error("HTTP response exceeds the size limit");
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  if (!url || !/^https?:\/\//i.test(url))
    throw new Error("valid http(s) url is required");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(url, {
      method: request.params?.httpMethod ?? "GET",
      headers: request.params?.headers,
      body: request.params?.body,
      signal: controller.signal,
    });
    const text = await readBoundedText(response);
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      /* plain text response */
    }
    return {
      items: [
        {
          status: response.status,
          headers: Object.fromEntries(response.headers),
          body,
        },
      ],
    };
  } finally {
    clearTimeout(timer);
  }
}

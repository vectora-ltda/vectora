export type Request = { method: "kernels"; params?: { url?: string; token?: string } };

export async function handle(request: Request): Promise<{ items: unknown[] }> {
  const url = request.params?.url;
  if (!url || !/^https:\/\//i.test(url)) throw new Error("valid HTTPS Jupyter URL is required");
  const response = await fetch(`${url.replace(/\/$/, "")}/api/kernels`, {
    headers: request.params?.token ? { Authorization: `token ${request.params.token}` } : {},
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`Jupyter API returned ${response.status}`);
  const data: unknown = await response.json();
  if (!Array.isArray(data)) throw new Error("Jupyter API returned invalid data");
  return { items: data.slice(0, 100) };
}

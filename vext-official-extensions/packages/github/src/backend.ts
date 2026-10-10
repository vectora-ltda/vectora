export type GitHubRequest = {
  method: "issues" | "pulls";
  params?: { owner?: string; repo?: string; state?: "open" | "closed" | "all" };
};

const NAME = /^[A-Za-z0-9_.-]{1,100}$/;
const MAX_ITEMS = 100;

function required(value: string | undefined, field: string): string {
  if (!value || !NAME.test(value)) throw new Error(`Invalid GitHub ${field}`);
  return value;
}

export async function handle(request: GitHubRequest): Promise<{ items: unknown[] }> {
  if (request.method !== "issues" && request.method !== "pulls") {
    throw new Error("Unsupported GitHub method");
  }
  const owner = required(request.params?.owner, "owner");
  const repo = required(request.params?.repo, "repo");
  const state = request.params?.state ?? "open";
  const resource = request.method === "issues" ? "issues" : "pulls";
  const response = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${resource}?state=${state}&per_page=${MAX_ITEMS}`,
    {
      headers: {
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(process.env.GITHUB_TOKEN ? { Authorization: `Bearer ${process.env.GITHUB_TOKEN}` } : {}),
      },
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) throw new Error(`GitHub API returned ${response.status}`);
  const data: unknown = await response.json();
  if (!Array.isArray(data)) throw new Error("GitHub API returned invalid data");
  return { items: data.slice(0, MAX_ITEMS) };
}

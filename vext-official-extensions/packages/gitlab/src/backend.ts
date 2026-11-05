export type GitLabRequest = {
  method: "issues" | "merge_requests";
  params?: {
    project?: string;
    state?: "opened" | "closed" | "all";
    host?: string;
  };
};

const PROJECT = /^[A-Za-z0-9_.-]+(?:%2F|\/)[A-Za-z0-9_.-]+$/;
const HOST = /^https:\/\/[^/]+$/i;

export async function handle(request: GitLabRequest): Promise<{ items: unknown[] }> {
  if (request.method !== "issues" && request.method !== "merge_requests")
    throw new Error("Unsupported GitLab method");
  const project = request.params?.project;
  if (!project || !PROJECT.test(project)) throw new Error("Invalid GitLab project");
  const host = request.params?.host ?? "https://gitlab.com";
  if (!HOST.test(host)) throw new Error("Invalid GitLab host");
  const resource = request.method === "issues" ? "issues" : "merge_requests";
  const response = await fetch(
    `${host}/api/v4/projects/${encodeURIComponent(project)}/${resource}?state=${request.params?.state ?? "opened"}&per_page=100`,
    {
      headers: {
        Accept: "application/json",
        ...(process.env.GITLAB_TOKEN ? { "PRIVATE-TOKEN": process.env.GITLAB_TOKEN } : {}),
      },
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) throw new Error(`GitLab API returned ${response.status}`);
  const data: unknown = await response.json();
  if (!Array.isArray(data)) throw new Error("GitLab API returned invalid data");
  return { items: data.slice(0, 100) };
}

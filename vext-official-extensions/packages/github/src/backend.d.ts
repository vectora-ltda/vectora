export type GitHubRequest = {
    method: "issues" | "pulls";
    params?: {
        owner?: string;
        repo?: string;
    };
};
export declare function handle(request: GitHubRequest): Promise<{
    items: unknown[];
}>;

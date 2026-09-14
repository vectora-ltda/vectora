export type DockerRequest = {
    method: "images" | "containers";
    params?: Record<string, unknown>;
};
export declare function handle(request: DockerRequest): Promise<{
    items: unknown[];
}>;

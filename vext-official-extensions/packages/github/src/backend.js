export async function handle(request) {
    if (request.method !== "issues" && request.method !== "pulls") {
        throw new Error("Unsupported GitHub method");
    }
    return { items: [] };
}

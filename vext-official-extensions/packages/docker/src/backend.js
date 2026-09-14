export async function handle(request) {
    if (request.method !== "images" && request.method !== "containers") {
        throw new Error("Unsupported Docker method");
    }
    return { items: [] };
}

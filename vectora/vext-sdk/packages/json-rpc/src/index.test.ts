import { createRequest, isRpcRequest } from "./index.js";
const request = createRequest(1, "ping", {});
if (!isRpcRequest(request)) throw new Error("request contract failed");

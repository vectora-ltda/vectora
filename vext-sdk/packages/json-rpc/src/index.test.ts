import {
  createRequest,
  isRpcRequest,
  parseMessage,
  serializeMessage,
} from "./index.js";
const request = createRequest(1, "ping", {});
if (!isRpcRequest(request)) throw new Error("request contract failed");
if (!isRpcRequest(parseMessage(serializeMessage(request))))
  throw new Error("round trip failed");
let rejected = false;
try {
  parseMessage(
    JSON.stringify({ jsonrpc: "2.0", id: 1, result: 1, error: "bad" }),
  );
} catch {
  rejected = true;
}
if (!rejected) throw new Error("invalid response accepted");

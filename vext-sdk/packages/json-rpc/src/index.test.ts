import {
  createCancel,
  createNotification,
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
if ((parseMessage(JSON.stringify(createCancel(1)) as string) as { method?: string }).method !== "$/cancelRequest")
  throw new Error("cancel message rejected");
if ((parseMessage(JSON.stringify(createNotification("progress", { value: 1 }))) as { method?: string }).method !== "progress")
  throw new Error("notification rejected");

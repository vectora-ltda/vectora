export type RpcId = number | string;
export interface RpcRequest<T = Record<string, unknown>> {
  jsonrpc: "2.0";
  id: RpcId;
  method: string;
  params: T;
}
export interface RpcSuccess<T = unknown> {
  jsonrpc: "2.0";
  id: RpcId;
  result: T;
}
export interface RpcFailure {
  jsonrpc: "2.0";
  id: RpcId | null;
  error: string;
}
export type RpcResponse<T = unknown> = RpcSuccess<T> | RpcFailure;
export function isRpcRequest(value: unknown): value is RpcRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as Partial<RpcRequest>;
  return (
    request.jsonrpc === "2.0" &&
    (typeof request.id === "string" || typeof request.id === "number") &&
    typeof request.method === "string" &&
    !!request.params &&
    typeof request.params === "object"
  );
}
export function createRequest<T extends Record<string, unknown>>(
  id: RpcId,
  method: string,
  params: T,
): RpcRequest<T> {
  return { jsonrpc: "2.0", id, method, params };
}

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

export const MAX_RPC_BYTES = 1024 * 1024;

export class RpcProtocolError extends Error {
  constructor(
    message: string,
    readonly code = "INVALID_REQUEST",
  ) {
    super(message);
    this.name = "RpcProtocolError";
  }
}
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

export function parseMessage(
  value: string,
  maxBytes = MAX_RPC_BYTES,
): RpcRequest | RpcResponse {
  if (new TextEncoder().encode(value).byteLength > maxBytes)
    throw new RpcProtocolError(
      "message exceeds maximum size",
      "MESSAGE_TOO_LARGE",
    );
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new RpcProtocolError("invalid JSON");
  }
  if (isRpcRequest(parsed)) return parsed;
  if (!parsed || typeof parsed !== "object")
    throw new RpcProtocolError("invalid RPC message");
  const response = parsed as Partial<RpcResponse>;
  if (
    response.jsonrpc !== "2.0" ||
    (typeof response.id !== "string" &&
      typeof response.id !== "number" &&
      response.id !== null)
  )
    throw new RpcProtocolError("invalid RPC response");
  if ("result" in response === "error" in response)
    throw new RpcProtocolError(
      "response must contain exactly one result or error",
    );
  if ("error" in response && typeof response.error !== "string")
    throw new RpcProtocolError("invalid RPC error");
  return response as RpcResponse;
}

export function serializeMessage(
  message: RpcRequest | RpcResponse,
  maxBytes = MAX_RPC_BYTES,
): string {
  const encoded = JSON.stringify(message);
  if (new TextEncoder().encode(encoded).byteLength > maxBytes)
    throw new RpcProtocolError(
      "message exceeds maximum size",
      "MESSAGE_TOO_LARGE",
    );
  return encoded;
}

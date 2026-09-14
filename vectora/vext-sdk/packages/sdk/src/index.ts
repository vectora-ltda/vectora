/** Public TypeScript contracts for Vectora VEXT extensions. */

export type {
  RpcFailure,
  RpcId,
  RpcRequest,
  RpcResponse,
  RpcSuccess,
} from "@vext/json-rpc";
export { createRequest, isRpcRequest } from "@vext/json-rpc";

export type VextRuntime = "node" | "python" | "none";

export type VextCapability =
  | "workspace.read"
  | "workspace.write"
  | "network"
  | "secrets.read"
  | "process.spawn";

export interface VextManifestInput {
  id: string;
  name: string;
  version: string;
  api_version: number;
  entrypoint: string;
  publisher?: string;
  protocol_version?: number;
  runtime?: VextRuntime;
  frontend_entrypoint?: string;
  backend_entrypoint?: string;
  permissions?: VextCapability[];
  platforms?: string[];
  contributions?: Record<string, unknown>;
}

export interface VextManifest extends VextManifestInput {
  publisher: string;
  protocol_version: number;
  runtime: VextRuntime;
  permissions: VextCapability[];
  platforms: string[];
  contributions: Record<string, unknown>;
  files: string[];
  integrity: string | null;
  provenance: Record<string, string>;
}

export interface VextRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params: Record<string, unknown>;
}

export interface VextResponse {
  jsonrpc: "2.0";
  id: number | string | null;
  result?: unknown;
  error?: string;
}

export interface VextContext {
  readonly manifest: VextManifest;
  request<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
  ): Promise<T>;
}

export function defineExtension<T extends VextManifestInput>(manifest: T): T {
  return manifest;
}

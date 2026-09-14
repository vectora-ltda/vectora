/** Public TypeScript contracts for Vectora VEXT extensions. */

export type {
  RpcFailure,
  RpcId,
  RpcRequest,
  RpcResponse,
  RpcSuccess,
} from "@vext/json-rpc";
export { createRequest, isRpcRequest } from "@vext/json-rpc";
import { createRequest } from "@vext/json-rpc";

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

export interface Disposable {
  dispose(): void;
}
export interface VextLogger {
  debug(message: string, fields?: Record<string, unknown>): void;
  info(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
  error(message: string, fields?: Record<string, unknown>): void;
}
export interface VextCommands {
  register(
    command: string,
    handler: (...args: unknown[]) => unknown | Promise<unknown>,
  ): Disposable;
  execute<T = unknown>(command: string, ...args: unknown[]): Promise<T>;
}
export interface VextState {
  get<T>(key: string, fallback?: T): T | undefined;
  set<T>(key: string, value: T): Promise<void>;
  delete(key: string): Promise<void>;
}

export const VEXT_PROTOCOL_VERSION = 1;

export function createContext(
  manifest: VextManifest,
  transport: (request: VextRequest) => Promise<VextResponse>,
): VextContext {
  let requestId = 0;
  return {
    manifest,
    async request<T = unknown>(
      method: string,
      params: Record<string, unknown> = {},
    ): Promise<T> {
      const response = await transport(
        createRequest(++requestId, method, params),
      );
      if (response.error) throw new Error(response.error);
      return response.result as T;
    },
  };
}

export function createCommandRegistry(): VextCommands {
  const handlers = new Map<
    string,
    (...args: unknown[]) => unknown | Promise<unknown>
  >();
  return {
    register(
      command: string,
      handler: (...args: unknown[]) => unknown | Promise<unknown>,
    ) {
      if (!/^[a-z][a-z0-9_.-]{1,127}$/.test(command))
        throw new Error("invalid command id");
      handlers.set(command, handler);
      return {
        dispose: () => {
          handlers.delete(command);
        },
      };
    },
    async execute<T = unknown>(
      command: string,
      ...args: unknown[]
    ): Promise<T> {
      const handler = handlers.get(command);
      if (!handler) throw new Error(`command not registered: ${command}`);
      return (await handler(...args)) as T;
    },
  };
}

export function createState(initial: Record<string, unknown> = {}): VextState {
  const values = new Map(Object.entries(initial));
  return {
    get: <T>(key: string, fallback?: T) =>
      values.has(key) ? (values.get(key) as T) : fallback,
    set: async <T>(key: string, value: T) => {
      values.set(key, value);
    },
    delete: async (key: string) => {
      values.delete(key);
    },
  };
}

export function defineExtension<T extends VextManifestInput>(manifest: T): T {
  return manifest;
}

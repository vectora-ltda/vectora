/**
 * Capacidades do perfil de browser por runtime.
 *
 * A UI do Vectora também roda no navegador comum. Nesse runtime não existe o
 * bridge Electron, então recursos que dependem da sessão nativa não podem ser
 * anunciados como disponíveis. Manter a matriz em um único módulo evita que
 * cada componente faça uma detecção diferente.
 */

export type BrowserRuntime = "desktop" | "web";

export type BrowserCapabilityId =
  | "profile-storage"
  | "permissions"
  | "downloads"
  | "password-manager-ui"
  | "cookies"
  | "history"
  | "popups";

export type BrowserCapabilityStatus = "available" | "unavailable";

export interface BrowserCapability {
  id: BrowserCapabilityId;
  status: BrowserCapabilityStatus;
}

const DESKTOP_CAPABILITIES: readonly BrowserCapability[] = [
  { id: "profile-storage", status: "available" },
  { id: "permissions", status: "available" },
  { id: "downloads", status: "available" },
  { id: "password-manager-ui", status: "available" },
  { id: "cookies", status: "available" },
  { id: "history", status: "available" },
  { id: "popups", status: "available" },
];

const WEB_CAPABILITIES: readonly BrowserCapability[] = DESKTOP_CAPABILITIES.map(
  (capability) => ({ ...capability, status: "unavailable" as const }),
);

/** Retorna uma cópia imutável da matriz adequada ao runtime atual. */
export function getBrowserCapabilityMatrix(
  runtime: BrowserRuntime,
): readonly BrowserCapability[] {
  return runtime === "desktop" ? DESKTOP_CAPABILITIES : WEB_CAPABILITIES;
}

/** Detecta o runtime sem acessar `window` durante SSR. */
export function getBrowserRuntime(hasDesktopBridge: boolean): BrowserRuntime {
  return hasDesktopBridge ? "desktop" : "web";
}

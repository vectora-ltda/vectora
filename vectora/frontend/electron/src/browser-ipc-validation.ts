import type { BrowserViewKind, ViewBounds } from "./browser-view-manager.js";

const PROFILE_ID = /^(?:default|session-[A-Za-z0-9_-]+)$/;
const MAX_PROFILE_ID_LENGTH = 256;
const MAX_URL_LENGTH = 8192;

export function isValidProfileId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_PROFILE_ID_LENGTH &&
    PROFILE_ID.test(value)
  );
}

export function isValidBrowserViewKind(
  value: unknown,
): value is BrowserViewKind {
  return value === "tab" || value === "native-settings";
}

export function isValidViewId(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

export function isValidViewBounds(value: unknown): value is ViewBounds {
  if (!value || typeof value !== "object") return false;
  const bounds = value as Partial<ViewBounds>;
  return [bounds.x, bounds.y, bounds.width, bounds.height].every(
    (part) => typeof part === "number" && Number.isFinite(part) && part >= 0,
  );
}

export function isValidBrowserUrl(
  value: unknown,
  kind: BrowserViewKind = "tab",
): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_URL_LENGTH
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    if (kind === "native-settings") {
      return (
        url.protocol === "chrome:" &&
        url.hostname === "settings" &&
        (url.pathname === "" ||
          url.pathname === "/" ||
          url.pathname.startsWith("/"))
      );
    }
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

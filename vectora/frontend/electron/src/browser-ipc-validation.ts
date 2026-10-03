import type { BrowserViewKind, ViewBounds } from "./browser-view-manager.js";

const PROFILE_ID = /^(?:default|session-[A-Za-z0-9_-]+)$/;
const MAX_PROFILE_ID_LENGTH = 256;
const MAX_URL_LENGTH = 8192;

/** Validate the same canonical HTTP origins and modes for creation and updates. */
export function isValidOriginPermissions(
  value: unknown,
): value is Record<string, "allow" | "deny"> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.entries(value).every(([origin, mode]) => {
    if (mode !== "allow" && mode !== "deny") return false;
    try {
      const url = new URL(origin);
      return isValidBrowserUrl(origin) && url.origin === origin;
    } catch {
      return false;
    }
  });
}

/** Accept only bounded partition identifiers, excluding separators and traversal. */
export function isValidProfileId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_PROFILE_ID_LENGTH &&
    PROFILE_ID.test(value)
  );
}

/** Restrict native views to the supported tab surface. */
export function isValidBrowserViewKind(
  value: unknown,
): value is BrowserViewKind {
  return value === "tab";
}

/** Reject nonpositive and fractional native view identifiers. */
export function isValidViewId(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

/** Accept finite nonnegative geometry for the native view. */
export function isValidViewBounds(value: unknown): value is ViewBounds {
  if (!value || typeof value !== "object") return false;
  const bounds = value as Partial<ViewBounds>;
  return [bounds.x, bounds.y, bounds.width, bounds.height].every(
    (part) => typeof part === "number" && Number.isFinite(part) && part >= 0,
  );
}

/** Restrict bounded browser navigation URLs to HTTP and HTTPS. */
export function isValidBrowserUrl(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_URL_LENGTH
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

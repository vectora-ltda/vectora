/** Stable opaque installation id used for cross-device thread activity. */
const STORAGE_KEY = "vectora-device-id";

export function getDeviceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing && /^vdev_[0-9a-f-]{36}$/.test(existing)) return existing;
    const uuid = globalThis.crypto?.randomUUID?.();
    if (!uuid) return null;
    const value = `vdev_${uuid}`;
    window.localStorage.setItem(STORAGE_KEY, value);
    return value;
  } catch {
    return null;
  }
}

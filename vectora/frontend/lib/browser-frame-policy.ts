/** Explicit denial also covers same-origin workspace frames where defaults allow access. */
export function browserFramePermissions(allowed: boolean): string {
  return ["camera", "microphone", "geolocation"]
    .map((feature) => `${feature} ${allowed ? "'src'" : "'none'"}`)
    .join("; ");
}

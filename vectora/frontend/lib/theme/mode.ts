import type { BaseThemeColors } from "@/lib/theme/presets";

const HEX_RE = /^#[0-9a-f]{6}$/i;

/** Classifies a theme from its background using relative sRGB luminance. */
export function classifyMode(
  colors: Pick<BaseThemeColors, "background">,
): "light" | "dark" {
  const background = colors.background?.trim() ?? "";
  if (!HEX_RE.test(background)) return "dark";
  const hex = background.slice(1);
  const channels = [0, 2, 4].map(
    (offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255,
  );
  const linear = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  const luminance =
    0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
  return luminance > 0.5 ? "light" : "dark";
}

/** Returns the WCAG contrast ratio between two six-digit hex colors. */
export function contrastRatio(first: string, second: string): number {
  const luminance = (value: string) => {
    if (!HEX_RE.test(value)) return 0;
    const channels = [0, 2, 4].map(
      (offset) =>
        Number.parseInt(value.slice(1 + offset, 3 + offset), 16) / 255,
    );
    const linear = channels.map((channel) =>
      channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    );
    return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
  };
  const a = luminance(first);
  const b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

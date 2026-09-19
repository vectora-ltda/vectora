/**
 * Allocates compact controls by progressively lowering a shared width ceiling.
 * The widest label yields space first; shorter labels keep their intrinsic
 * width until the ceiling reaches them.
 */
export function balanceCompactControlWidths(
  naturalWidths: number[],
  availableWidth: number,
): number[] {
  const totalNaturalWidth = naturalWidths.reduce(
    (sum, width) => sum + width,
    0,
  );
  if (availableWidth >= totalNaturalWidth || availableWidth <= 0) {
    return naturalWidths;
  }

  let low = 0;
  let high = Math.max(...naturalWidths);
  for (let iteration = 0; iteration < 32; iteration += 1) {
    const ceiling = (low + high) / 2;
    const used = naturalWidths.reduce(
      (sum, width) => sum + Math.min(width, ceiling),
      0,
    );
    if (used > availableWidth) high = ceiling;
    else low = ceiling;
  }

  return naturalWidths.map((width) => Math.min(width, low));
}

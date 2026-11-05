/**
 * Timing/easing compartilhado do motion (framer-motion) — mesma constante
 * usada pelo workbench (troca de aba) e pela sidebar (collapse/expand,
 * grupos de workspace), pra manter o "feel" consistente entre as duas
 * áreas em vez de cada componente inventar o próprio timing.
 */
export const PANEL_TRANSITION = {
  duration: 0.14,
  ease: [0.4, 0, 0.2, 1] as const,
};

/** Small status indicator transition; reduced motion replaces it with instant. */
export const PENDING_BADGE_TRANSITION = {
  type: "spring" as const,
  stiffness: 380,
  damping: 18,
};

export const MOTION_INSTANT = { duration: 0 } as const;

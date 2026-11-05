/** Shared geometry constants for the resizable side columns. */

/**
 * Minimum width for an expanded side column.
 *
 * This matches Tailwind's `min-w-60` token (15rem, approximately 240px),
 * keeping the sessions and chat panels wide enough for their controls.
 */
export const SIDE_COLUMN_MIN_WIDTH = 240;

/** Piso do painel de chat quando está aberto no modo IDE. */
export const CHAT_SIDEBAR_OPEN_MIN_WIDTH = SIDE_COLUMN_MIN_WIDTH;

/** Largura da rail quando o painel de chat está recolhido. */
export const COLLAPSED_RAIL_WIDTH = 48;

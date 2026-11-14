/**
 * Chat Utilities
 *
 * Centralized exports for all chat-related utilities
 */

// Message helpers
export {
  generateMessageId,
  createUserMessage,
  updateMessageInList,
  ensureMessageExists,
  historyMessageToMessage,
} from "./message-helpers";

// Content helpers
export { extractTextFromContent } from "./content-helpers";

// Attachments (F1 — multimodal)
export { toApiAttachments } from "./attachments";

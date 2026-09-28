/**
 * Message Creation and Manipulation Utilities
 *
 * Functions for creating and updating messages in chat conversations.
 */

import type { Message } from "../../types";
import type { HistoryMessage } from "../../api/vectora-client";

// ============================================================================
// Message ID Generation
// ============================================================================

let messageIdCounter = 0;

/**
 * Generate a unique message ID.
 * Uses timestamp + counter to ensure uniqueness even for rapid message creation.
 */
export const generateMessageId = (): string => {
  const timestamp = Date.now();
  const counter = messageIdCounter++;
  return `${timestamp}-${counter}`;
};

// ============================================================================
// Message Creation
// ============================================================================

/**
 * Create a new user message.
 */
export const createUserMessage = (content: string): Message => ({
  id: generateMessageId(),
  role: "user",
  content,
  timestamp: new Date(),
});

// ============================================================================
// Message List Manipulation
// ============================================================================

/**
 * Update a specific message in a message list.
 * Returns a new array with the updated message.
 */
export const updateMessageInList = (
  messages: Message[],
  messageId: string,
  updates: Partial<Message> | ((m: Message) => Partial<Message>),
): Message[] => {
  return messages.map((m) => {
    if (m.id !== messageId) return m;
    const patch = typeof updates === "function" ? updates(m) : updates;
    return { ...m, ...patch };
  });
};

/**
 * Ensure a message exists in the list.
 * If the message doesn't exist, appends it to the end.
 */
export const ensureMessageExists = (
  messages: Message[],
  messageId: string,
  baseMessage: Message,
): Message[] => {
  const existing = messages.find((m) => m.id === messageId);
  return existing ? messages : [...messages, baseMessage];
};

// ============================================================================
// History Conversion
// ============================================================================

/**
 * Convert one `HistoryMessage` (REST history page) into a UI `Message`.
 *
 * Image attachments only carry over when `url` is populated — that's the
 * persisted file path (`GET /threads/{id}/attachments/{filename}`), the
 * only thing that survives a backend restart. Attachments without a URL
 * (persistence failed, or messages from before this field existed) drop
 * silently; the turn's text is still shown.
 */
export const historyMessageToMessage = (
  hist: HistoryMessage,
  id: string,
): Message => {
  const images = hist.attachments
    ?.filter((att) => att.kind === "image" && att.url)
    .map((att) => ({
      id: att.url as string,
      url: att.url as string,
      mimeType: att.mimeType,
      name: att.name,
      size: att.size,
    }));

  return {
    id,
    role: hist.role === "human" ? "user" : "assistant",
    content: hist.content,
    timestamp: hist.created_at ? new Date(hist.created_at) : new Date(),
    checkpointId: hist.checkpoint_id,
    ...(images?.length ? { images } : {}),
    ...(hist.edited_files?.length
      ? {
          editedFiles: hist.edited_files.map((file) => ({
            path: file.path,
            status: file.status,
            additions: file.additions,
            deletions: file.deletions,
            hunks: file.hunks,
          })),
        }
      : {}),
  } as Message;
};

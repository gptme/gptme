import type { Message } from '@/types/conversation';

export interface ExportMarkdownOptions {
  includeSystem?: boolean;
  includeTimestamps?: boolean;
  /** Include <thinking>/<think> reasoning blocks in assistant messages. Default: false. */
  includeThinking?: boolean;
  /** Include tool messages (tool calls + results). Default: true. */
  includeTools?: boolean;
}

export interface ImportedConversationData {
  name: string;
  messages: Message[];
}

const importableRoles = new Set<Message['role']>(['system', 'user', 'assistant']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Strip <thinking> / <think> blocks from assistant message content.
 * Matches the same pattern used by the TTS pipeline and markdown renderer.
 */
function stripThinkingBlocks(content: string): string {
  return content
    .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/g, '')
    .replace(/^\n+/, '')
    .trim();
}

export function getExportableMessages(
  messages: Message[],
  options?: Pick<ExportMarkdownOptions, 'includeSystem' | 'includeTools'>
): Message[] {
  const { includeSystem = false, includeTools = true } = options ?? {};
  return messages.filter((msg) => {
    if (msg.hide) return false;

    // Check if this is a tool-metadata system message
    const isToolSystemMessage =
      msg.role === 'system' &&
      msg.content &&
      (msg.content.includes('[Tool:') ||
        msg.content.includes('```tool') ||
        msg.content.includes('<tool'));

    // Filter tool-related messages (tool, tool_result, and tool-metadata system messages)
    if (!includeTools) {
      if (msg.role === 'tool' || msg.role === 'tool_result' || isToolSystemMessage) return false;
    }

    // Filter regular system messages (those not containing tool metadata)
    if (!includeSystem && msg.role === 'system' && !isToolSystemMessage) return false;

    return true;
  });
}

function getImportableMessages(messages: Message[]): Message[] {
  return getExportableMessages(messages, { includeSystem: true }).filter((msg) =>
    importableRoles.has(msg.role)
  );
}

/**
 * Format a conversation's messages as a Markdown document.
 */
export function formatConversationAsMarkdown(
  name: string,
  messages: Message[],
  options?: ExportMarkdownOptions
): string {
  const {
    includeTimestamps = true,
    includeThinking = false,
    includeSystem = false,
    includeTools = true,
  } = options ?? {};

  const lines: string[] = [`# ${name}`, ''];

  for (const msg of getExportableMessages(messages, { includeSystem, includeTools })) {
    const roleLabel = msg.role.charAt(0).toUpperCase() + msg.role.slice(1);
    let header = `## ${roleLabel}`;
    if (includeTimestamps && msg.timestamp) {
      header += `  \n*${msg.timestamp}*`;
    }
    lines.push(header, '');

    const content =
      !includeThinking && msg.role === 'assistant' ? stripThinkingBlocks(msg.content) : msg.content;

    lines.push(content, '');
  }

  return lines.join('\n');
}

/**
 * Copy a conversation's messages to the clipboard as Markdown.
 * Returns a promise that resolves when the content is in the clipboard.
 *
 * Note: This copies only the currently loaded messages. For large conversations
 * with pagination/virtual scrolling, older messages that haven't been loaded yet
 * will not be included. Call from ConversationSettings after ensuring all messages
 * have been loaded (or check hasMoreBefore in the conversation state).
 */
export async function copyConversationToClipboard(
  name: string,
  messages: Message[],
  options?: ExportMarkdownOptions
): Promise<void> {
  const markdown = formatConversationAsMarkdown(name, messages, options);
  await navigator.clipboard.writeText(markdown);
}

/**
 * Trigger a file download in the browser.
 */
export function downloadAsFile(content: string, filename: string, mimeType = 'text/markdown') {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Export a conversation as a Markdown file download.
 */
export function exportConversationAsMarkdown(
  conversationId: string,
  name: string,
  messages: Message[],
  options?: ExportMarkdownOptions
) {
  const markdown = formatConversationAsMarkdown(name, messages, options);
  // Sanitize filename: replace unsafe characters with dashes
  const safeName = (name || conversationId)
    .replace(/[^a-zA-Z0-9_\-. ]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 100);
  downloadAsFile(markdown, `${safeName}.md`);
}

/**
 * Export a conversation as a JSON file download.
 */
export function exportConversationAsJSON(
  conversationId: string,
  name: string,
  messages: Message[]
) {
  const data = {
    id: conversationId,
    name,
    exported_at: new Date().toISOString(),
    messages: getImportableMessages(messages),
  };
  const json = JSON.stringify(data, null, 2);
  const safeName = (name || conversationId)
    .replace(/[^a-zA-Z0-9_\-. ]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 100);
  downloadAsFile(json, `${safeName}.json`, 'application/json');
}

export function parseConversationImportJSON(json: string): ImportedConversationData {
  let parsed: unknown;

  try {
    parsed = JSON.parse(json);
  } catch {
    throw new Error('Invalid JSON file');
  }

  if (!isRecord(parsed)) {
    throw new Error('Conversation import must be a JSON object');
  }

  if ('name' in parsed && parsed.name != null && typeof parsed.name !== 'string') {
    throw new Error('Conversation import name must be a string');
  }

  if ('id' in parsed && parsed.id != null && typeof parsed.id !== 'string') {
    throw new Error('Conversation import id must be a string');
  }

  if (!Array.isArray(parsed.messages)) {
    throw new Error('Conversation import must include a messages array');
  }

  const messages = parsed.messages.map((message, index) => {
    if (!isRecord(message)) {
      throw new Error(`Imported message ${index + 1} must be an object`);
    }

    const { role, content, timestamp } = message;

    if (role === 'tool') {
      return null;
    }

    if (typeof role !== 'string' || !importableRoles.has(role as Message['role'])) {
      const roleLabel = typeof role === 'string' ? `"${role}"` : 'a valid role';
      throw new Error(
        `Imported message ${index + 1} has unsupported role ${roleLabel}. Only system, user, and assistant messages can be restored.`
      );
    }

    if (typeof content !== 'string') {
      throw new Error(`Imported message ${index + 1} is missing a string content field`);
    }

    if (timestamp !== undefined && typeof timestamp !== 'string') {
      throw new Error(`Imported message ${index + 1} has an invalid timestamp`);
    }

    return {
      role: role as Message['role'],
      content,
      ...(timestamp !== undefined ? { timestamp } : {}),
    };
  });

  return {
    name:
      typeof parsed.name === 'string'
        ? parsed.name
        : typeof parsed.id === 'string'
          ? parsed.id
          : '',
    messages: messages.filter((message): message is Message => message !== null),
  };
}

import type { Message } from '@/types/conversation';

export interface ExportMarkdownOptions {
  includeSystem?: boolean;
  includeTimestamps?: boolean;
}

export interface ImportedConversationData {
  name: string;
  messages: Message[];
}

const importableRoles = new Set<Message['role']>(['system', 'user', 'assistant']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function getExportableMessages(
  messages: Message[],
  options?: Pick<ExportMarkdownOptions, 'includeSystem'>
): Message[] {
  const { includeSystem = false } = options ?? {};
  return messages.filter((msg) => !msg.hide && (includeSystem || msg.role !== 'system'));
}

/**
 * Format a conversation's messages as a Markdown document.
 */
export function formatConversationAsMarkdown(
  name: string,
  messages: Message[],
  options?: ExportMarkdownOptions
): string {
  const { includeTimestamps = true } = options ?? {};

  const lines: string[] = [`# ${name}`, ''];

  for (const msg of getExportableMessages(messages, options)) {
    const roleLabel = msg.role.charAt(0).toUpperCase() + msg.role.slice(1);
    let header = `## ${roleLabel}`;
    if (includeTimestamps && msg.timestamp) {
      header += `  \n*${msg.timestamp}*`;
    }
    lines.push(header, '');
    lines.push(msg.content, '');
  }

  return lines.join('\n');
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
    messages,
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
      (typeof parsed.name === 'string' && parsed.name) ||
      (typeof parsed.id === 'string' ? parsed.id : ''),
    messages,
  };
}

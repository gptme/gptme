import type { Message } from '@/types/conversation';

export interface ExportMarkdownOptions {
  includeSystem?: boolean;
  includeTimestamps?: boolean;
  includeThinking?: boolean;
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
 * Strip thinking/reasoning blocks from assistant message content.
 * Handles <thinking>/<think> tags as well as the `<think redacted>` variant
 * gptme emits for Anthropic's RedactedThinkingBlock (see llm_anthropic.py).
 * Also strips a trailing *unclosed* thinking tag: if generation is
 * interrupted mid-thinking, gptme never streams the closing `</think>`, so
 * the reasoning would otherwise leak into the exported "response" content.
 */
export function stripThinkingBlocks(content: string): string {
  return content
    .replace(/<think(?:ing)?(?: redacted)?>[\s\S]*?<\/think(?:ing)?(?: redacted)?>\n?/g, '')
    .replace(/<think(?:ing)?(?: redacted)?>[\s\S]*$/, '')
    .trim();
}

// Built-in gptme tool block types (markdown codeblock langtags that represent
// a tool invocation rather than example code). Mirrors block_types across
// gptme/tools/*.py.
const TOOL_BLOCK_TYPES = new Set([
  'clarify',
  'complete',
  'choice',
  'gh',
  'elicit',
  'form',
  'patch',
  'patch_many',
  'morph',
  'view_anchored',
  'patch_anchored',
  'progress',
  'hashline_edit',
  'ipython',
  'py',
  'mcp',
  'todo',
  'save',
  'append',
  'vent',
  'shell',
  'tmux',
  'read',
  'restart',
]);

// MCP server tools register block types dynamically as `{server}.{tool}`
// (see gptme/tools/mcp_adapter.py: `block_types=[f"{server_config.name}.{mcp_tool.name}"]`),
// so they can't be enumerated in TOOL_BLOCK_TYPES ahead of time.
const MCP_BLOCK_TYPE_RE = /^[\w-]+\.[\w-]+$/;

function isToolBlockTag(tag: string): boolean {
  return TOOL_BLOCK_TYPES.has(tag) || MCP_BLOCK_TYPE_RE.test(tag);
}

/**
 * Strip fenced tool-invocation codeblocks (```shell, ```save path, ```server.tool, etc.)
 * from assistant message content.
 */
function stripFencedToolBlocks(content: string): string {
  return content.replace(/```([^\n`]*)\n[\s\S]*?```/g, (block, langLine: string) => {
    const tag = langLine.trim().split(/\s+/)[0]?.toLowerCase();
    return tag && isToolBlockTag(tag) ? '' : block;
  });
}

/**
 * Strip `@tool(id): {...}` / `@tool: {...}` invocation lines (the non-markdown
 * "tool" format, and the live-streaming shape) from assistant message content.
 * Scans braces rather than regex-matching JSON so pretty-printed or compact
 * argument objects are both handled correctly.
 */
function stripAtFormatToolCalls(content: string): string {
  const lines = content.split('\n');
  const callPrefix = /^@[\w.-]+(?:\([^)\n]*\))?:\s*/;
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const match = lines[i].match(callPrefix);
    if (!match) {
      out.push(lines[i]);
      i++;
      continue;
    }
    const rest = lines[i].slice(match[0].length);
    if (!rest.startsWith('{')) {
      // Not a JSON tool call — preserve the line as-is.
      out.push(lines[i]);
      i++;
      continue;
    }
    let depth = 0;
    let sawOpen = false;
    let inString = false;
    let escapeNext = false;
    let j = i;
    let trailingText = '';
    outer: for (; j < lines.length; j++) {
      const scanLine = j === i ? rest : lines[j];
      for (let k = 0; k < scanLine.length; k++) {
        const ch = scanLine[k];
        if (escapeNext) {
          escapeNext = false;
          continue;
        }
        if (ch === '\\' && inString) {
          escapeNext = true;
          continue;
        }
        if (ch === '"') {
          inString = !inString;
          continue;
        }
        if (inString) continue;
        if (ch === '{') {
          depth++;
          sawOpen = true;
        } else if (ch === '}') {
          depth--;
        }
        if (sawOpen && depth <= 0) {
          trailingText = scanLine.slice(k + 1).trim();
          j++;
          break outer;
        }
      }
    }
    if (trailingText) {
      out.push(trailingText);
    }
    i = j;
  }
  return out.join('\n');
}

/**
 * Strip tool invocations embedded directly in assistant message content
 * (fenced tool codeblocks and `@tool: {...}` calls). Distinct from filtering
 * `role: 'tool'` result messages, which getExportableMessages handles.
 */
function stripToolInvocations(content: string): string {
  return stripAtFormatToolCalls(stripFencedToolBlocks(content));
}

export function getExportableMessages(
  messages: Message[],
  options?: Pick<ExportMarkdownOptions, 'includeSystem' | 'includeTools'>
): Message[] {
  const { includeSystem = false, includeTools = true } = options ?? {};
  return messages.filter(
    (msg) =>
      !msg.hide && (includeSystem || msg.role !== 'system') && (includeTools || msg.role !== 'tool')
  );
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
  const { includeTimestamps = true, includeThinking = true, includeTools = true } = options ?? {};

  const lines: string[] = [`# ${name}`, ''];

  for (const msg of getExportableMessages(messages, options)) {
    const roleLabel = msg.role.charAt(0).toUpperCase() + msg.role.slice(1);
    let header = `## ${roleLabel}`;
    if (includeTimestamps && msg.timestamp) {
      header += `  \n*${msg.timestamp}*`;
    }
    lines.push(header, '');
    let content = msg.content;
    if (msg.role === 'assistant') {
      if (!includeThinking) content = stripThinkingBlocks(content);
      if (!includeTools) content = stripToolInvocations(content);
    }
    lines.push(content, '');
  }

  return lines.join('\n');
}

/**
 * Copy the full conversation to the clipboard as Markdown.
 * Reads from the conversation store, not the DOM, so virtualized messages are included.
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

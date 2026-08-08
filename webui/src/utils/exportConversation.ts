import type { Message } from '@/types/conversation';

export interface ExportMarkdownOptions {
  includeSystem?: boolean;
  includeTimestamps?: boolean;
  /** Include <thinking>...</thinking> reasoning blocks in assistant messages. Default: false */
  includeThinking?: boolean;
  /** Include tool call messages (role: 'tool'). Default: true */
  includeToolCalls?: boolean;
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
  options?: Pick<ExportMarkdownOptions, 'includeSystem' | 'includeToolCalls'>
): Message[] {
  const { includeSystem = false, includeToolCalls = true } = options ?? {};
  return messages.filter(
    (msg) =>
      !msg.hide &&
      (includeSystem || msg.role !== 'system') &&
      (includeToolCalls || msg.role !== 'tool')
  );
}

/** Strip <thinking>...</thinking> and <think>...</think> blocks from a string. */
function stripThinkingBlocks(content: string): string {
  return content.replace(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/g, '').trim();
}

/**
 * Fenced-code-block languages that are ordinary prose/code examples, never
 * gptme tool invocations. Deliberately NOT the inverse (a list of known tool
 * names): gptme's tool set is open-ended — MCP servers register their own
 * block type per connected server as `${serverName}.${toolName}` (see
 * `gptme/tools/mcp_adapter.py`), so any fixed list of *tool* names can never
 * be exhaustive. Matching against a list of known-safe languages instead
 * fails closed: an unrecognized language (including any MCP tool name) is
 * treated as a possible tool invocation and stripped.
 */
const SAFE_LANGUAGES = new Set([
  'text',
  'txt',
  'plaintext',
  'markdown',
  'md',
  'json',
  'yaml',
  'yml',
  'toml',
  'xml',
  'csv',
  'ini',
  'env',
  'diff',
  'javascript',
  'js',
  'jsx',
  'typescript',
  'ts',
  'tsx',
  'html',
  'css',
  'scss',
  'less',
  'sql',
  'graphql',
  'proto',
  'rust',
  'go',
  'golang',
  'java',
  'c',
  'cpp',
  'c++',
  'csharp',
  'cs',
  'ruby',
  'rb',
  'php',
  'swift',
  'kotlin',
  'scala',
  'elixir',
  'erlang',
  'haskell',
  'clojure',
  'lua',
  'perl',
  'dart',
  'dockerfile',
  'makefile',
  'cmake',
  'nginx',
  'vue',
  'svelte',
]);

/**
 * Strip tool-invocation code blocks from assistant content.
 *
 * Matches fences of 3-or-more backticks. The closing fence must have at least
 * as many backticks as the opener (backreference + zero-or-more additional
 * backticks) to prevent an embedded shorter fence inside a tool block from
 * being treated as the close and leaking the remainder of the tool content.
 * As a consequence, gptme "recovered adjacent-fence" forms (where the closer
 * has fewer backticks than the opener) are not matched; those are rare
 * parser-recovery cases and the tradeoff favours preventing content leakage.
 */
function stripToolCallBlocks(content: string): string {
  return content
    .replace(
      /^(`{3,})\s*([^\s`\n]+)(?:[ \t][^\n]*)?\n[\s\S]*?^\1`*\s*$/gm,
      (block, _backticks, lang: string) => (SAFE_LANGUAGES.has(lang.toLowerCase()) ? block : '')
    )
    .trim();
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
    includeToolCalls = true,
  } = options ?? {};

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
      if (!includeToolCalls) content = stripToolCallBlocks(content);
      if (!includeThinking) content = stripThinkingBlocks(content);
    }
    lines.push(content, '');
  }

  return lines.join('\n');
}

/**
 * Copy a conversation's trajectory to the clipboard as Markdown.
 * Returns true on success, throws on clipboard access failure.
 */
export async function copyConversationAsMarkdown(
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

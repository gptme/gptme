import { toast } from 'sonner';
import { Lexer, Tokenizer } from 'marked';
import { scanMarkdownFences } from './markdownFences';
import type { Message } from '@/types/conversation';
import { isTauriEnvironment } from '@/utils/tauri';

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

/** Strip reasoning using the same literal-code boundaries as filtered exports. */
export function stripThinkingBlocks(content: string): string {
  return filterAssistantContent(content, false, true).trim();
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
  'memory',
  'watch',
  'request_tool_change',
]);

// MCP server tools register block types dynamically as `{server}.{tool}`
// (see gptme/tools/mcp_adapter.py: `block_types=[f"{server_config.name}.{mcp_tool.name}"]`),
// so they can't be enumerated in TOOL_BLOCK_TYPES ahead of time.
const MCP_BLOCK_TYPE_RE = /^[\w-]+\.[\w-]+$/;

function isToolBlockTag(tag: string): boolean {
  return TOOL_BLOCK_TYPES.has(tag) || MCP_BLOCK_TYPE_RE.test(tag);
}

/** Bound inline parsing by Markdown blocks, not just the next closing backtick. */
function inlineParagraphEnd(tokenizer: Tokenizer, content: string, start: number): number {
  // ATX headings end at their own newline even when the first backtick is
  // mid-heading. Starting paragraph grammar at that tick loses the prefix.
  const lineStart = content.lastIndexOf('\n', start - 1) + 1;
  const quotePrefix = content.slice(lineStart, start).match(/^(?: {0,3}>[ \t]?)+/)?.[0];
  if (quotePrefix) {
    // Markdown parses inline spans in the quote's text, not its `>` markers.
    // Strip only the enclosing depth; a deeper quote still interrupts the
    // paragraph. Map the logical boundary back to the untouched source bytes.
    const depth = quotePrefix.split('>').length - 1;
    const prefixes = new RegExp(`^(?: {0,3}>[ \\t]?){1,${depth}}`);
    const offsets: { logical: number; source: number }[] = [];
    const lines: string[] = [];
    let logical = 0;
    let source = lineStart;
    while (source < content.length) {
      const next = content.indexOf('\n', source);
      const end = next === -1 ? content.length : next + 1;
      const line = content.slice(source, end);
      const prefix = line.match(prefixes)?.[0] ?? '';
      // Missing quote markers can be lazy continuation of the same paragraph.
      const text = line.slice(prefix.length);
      if (source > lineStart && /^[ \t]*\r?\n$/.test(text)) break;
      offsets.push({ logical, source: source + prefix.length });
      lines.push(text);
      logical += text.length;
      source = end;
    }
    const boundary = inlineParagraphEnd(
      tokenizer,
      lines.join(''),
      start - lineStart - quotePrefix.length
    );
    let offset = offsets[0];
    for (const entry of offsets) {
      if (entry.logical > boundary) break;
      offset = entry;
    }
    return offset.source + boundary - offset.logical;
  }
  const newline = content.indexOf('\n', start);
  const lineEnd = newline === -1 ? content.length : newline;
  if (/^ {0,3}#{1,6}(?:[ \t]|$)/.test(content.slice(lineStart, lineEnd))) return lineEnd;
  const remaining = content.slice(start);
  // Use marked's paragraph grammar without tokenizing/rendering its contents.
  const paragraph = tokenizer.rules.block.paragraph.exec(remaining)?.[0] ?? remaining;
  // Setext headings may contain multiple inline lines, but their underline
  // separates them from the body even without a blank line. Limit this check
  // to the paragraph window rather than rescanning all subsequent paragraphs.
  const headingSource = content.slice(lineStart, start) + paragraph;
  const heading = tokenizer.rules.block.lheading.exec(headingSource.replace(/\r\n/g, '\n'));
  if (heading) {
    let end = 0;
    let normalized = 0;
    while (normalized < heading[1].length) {
      if (headingSource[end] === '\r' && headingSource[end + 1] === '\n') end++;
      end++;
      normalized++;
    }
    if (start < lineStart + end) return lineStart + end;
  }
  // marked expects normalized LF input; exports preserve CRLF. Transcript
  // fences also accept tabs/arbitrary indentation, unlike CommonMark fences.
  const boundary = paragraph.search(
    /\r?\n(?:[ \t]*\r?\n|[ \t]*(?:`{3,}[^`\r\n]*|~{3,}[^\r\n]*)(?:\r?\n|$))/
  );
  return start + (boundary === -1 ? paragraph.length : boundary);
}

/** End of a native JSON-object invocation, including interrupted arguments. */
function nativeCallEnd(content: string, start: number): number {
  let depth = 0;
  let inString = false;
  let escapeNext = false;
  for (let i = start; i < content.length; i++) {
    const ch = content[i];
    if (escapeNext) {
      escapeNext = false;
    } else if (ch === '\\' && inString) {
      escapeNext = true;
    } else if (ch === '"') {
      inString = !inString;
    } else if (!inString) {
      if (ch === '{') depth++;
      if (ch === '}' && --depth === 0) return i + 1;
    }
  }
  return content.length;
}

/**
 * Classify original source once: retained payloads are opaque too, so disabling
 * one category cannot expose syntax in another. Literal fences/code spans keep
 * their exact bytes. Top-level tool-tag fences and @name: JSON objects count as
 * invocations even when nearby prose calls them examples; unfinished blocks
 * extend to EOF. This is transcript filtering, not intent inference/redaction.
 */
function filterAssistantContent(
  content: string,
  includeThinking: boolean,
  includeTools: boolean
): string {
  if (includeThinking && includeTools) return content;

  const tokenizer = new Tokenizer();
  // Lexer installs marked's inline-code rules on the tokenizer (including
  // variable-length and multiline code spans), without rendering/normalizing.
  new Lexer({ tokenizer });
  const out: string[] = [];
  let i = 0;
  let paragraphEnd = 0;
  while (i < content.length) {
    // Discover fences only after skipping opaque thinking/native payloads: a
    // fence inside retained JSON/reasoning must not capture later answer text.
    if ((i === 0 || content[i - 1] === '\n') && /^[ \t]*(`{3,}|~{3,})/.test(content.slice(i))) {
      const fence = scanMarkdownFences(content, true, i)[0];
      if (fence) {
        const tag = fence.tag.split(/\s+/)[0].toLowerCase();
        if (includeTools || !isToolBlockTag(tag)) out.push(content.slice(i, fence.end));
        i = fence.end;
        continue;
      }
    }
    // Markdown escapes consume the following punctuation before inline parsing.
    if (content[i] === '\\' && /[\\`]/.test(content[i + 1] ?? '')) {
      out.push(content.slice(i, i + 2));
      i += 2;
      continue;
    }
    if (content[i] === '`') {
      // Reuse the boundary for all spans in this paragraph rather than
      // searching the remaining message again for every short inline span.
      if (i >= paragraphEnd) paragraphEnd = inlineParagraphEnd(tokenizer, content, i);
      const code = tokenizer.codespan(content.slice(i, paragraphEnd));
      if (code) {
        out.push(code.raw);
        i += code.raw.length;
        continue;
      }
      // An unmatched run is prose, not a shorter code-span opener.
      const run = content.slice(i).match(/^`+/)![0];
      out.push(run);
      i += run.length;
      continue;
    }
    const thinking = content[i] === '<' && content.slice(i).match(/^<think(?:ing)?(?: redacted)?>/);
    if (thinking) {
      const closing = /<\/think(?:ing)?(?: redacted)?>/g;
      closing.lastIndex = i + thinking[0].length;
      const close = closing.exec(content);
      let end = close ? close.index + close[0].length : content.length;
      if (!includeThinking && content[end] === '\n') end++;
      if (includeThinking) out.push(content.slice(i, end));
      i = end;
      continue;
    }
    if (i === 0 || content[i - 1] === '\n') {
      const call = content.slice(i).match(/^@[\w.-]+(?:\([^)\n]*\))?:[^\S\n]*(?=\{)/);
      if (call) {
        let end = nativeCallEnd(content, i + call[0].length);
        if (includeTools) out.push(content.slice(i, end));
        else {
          // Keep same-line answer text, but omit the removed call's empty line.
          while (content[end] === ' ' || content[end] === '\t' || content[end] === '\r') end++;
          if (content[end] === '\n') end++;
        }
        i = end;
        continue;
      }
    }
    out.push(content[i]);
    i++;
  }
  return out.join('');
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
      content = filterAssistantContent(content, includeThinking, includeTools);
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

export type DownloadOutcome =
  | { kind: 'browser' }
  | { kind: 'tauri'; success: true; path: string | null }
  | { kind: 'tauri'; success: false; error: string };

const TAURI_DOWNLOAD_FINISHED_EVENT = 'gptme-download-finished';
const TAURI_DOWNLOAD_TIMEOUT_MS = 60_000;

interface TauriDownloadFinishedPayload {
  url: string;
  path: string | null;
  success: boolean;
  error?: string | null;
}

function triggerAnchorDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

/**
 * Trigger a download and wait for Tauri to report the actual saved path.
 *
 * The webview's download handler emits `gptme-download-finished` only after
 * the native download either finishes or fails, so the UI must not claim
 * success before that event arrives.
 */
async function downloadInTauri(
  content: string,
  filename: string,
  mimeType: string
): Promise<DownloadOutcome> {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);

  return await new Promise<DownloadOutcome>((resolve) => {
    let settled = false;
    let unlisten: (() => void) | undefined;

    const finish = (outcome: DownloadOutcome) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      unlisten?.();
      URL.revokeObjectURL(url);
      resolve(outcome);
    };

    const timeout = window.setTimeout(() => {
      finish({
        kind: 'tauri',
        success: false,
        error: 'Timed out waiting for the file to finish saving.',
      });
    }, TAURI_DOWNLOAD_TIMEOUT_MS);

    void (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisten = await listen<TauriDownloadFinishedPayload>(
          TAURI_DOWNLOAD_FINISHED_EVENT,
          (event) => {
            const payload = event.payload;
            // Exports are short-lived and sequential, but match the blob URL
            // so a concurrent download cannot settle the wrong promise.
            if (payload.url !== url) return;
            if (payload.success) {
              finish({ kind: 'tauri', success: true, path: payload.path ?? null });
            } else {
              finish({
                kind: 'tauri',
                success: false,
                error: payload.error ?? 'The file could not be saved.',
              });
            }
          }
        );
      } catch (error) {
        finish({
          kind: 'tauri',
          success: false,
          error:
            error instanceof Error ? error.message : 'Could not listen for the download to finish.',
        });
        return;
      }

      // The timeout may have fired while `listen` was being registered.
      if (settled) {
        unlisten();
        return;
      }
      triggerAnchorDownload(url, filename);
    })();
  });
}

/**
 * Trigger a file download.
 *
 * In a browser this returns synchronously after clicking the anchor. In Tauri
 * it resolves only after the native download handler reports the final path.
 */
export function downloadAsFile(
  content: string,
  filename: string,
  mimeType = 'text/markdown'
): DownloadOutcome | Promise<DownloadOutcome> {
  if (isTauriEnvironment()) {
    return downloadInTauri(content, filename, mimeType);
  }

  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  triggerAnchorDownload(url, filename);
  URL.revokeObjectURL(url);
  return { kind: 'browser' };
}

/**
 * Report an export outcome once it is known. For Tauri downloads this happens
 * after `DownloadEvent::Finished`, never before.
 */
export function reportDownloadOutcome(
  outcome: DownloadOutcome | Promise<DownloadOutcome>,
  browserSuccessMessage: string
): void {
  if (outcome instanceof Promise) {
    void outcome.then((resolved) => reportDownloadOutcome(resolved, browserSuccessMessage));
    return;
  }

  if (outcome.kind === 'browser') {
    toast.success(browserSuccessMessage);
    return;
  }

  if (!outcome.success) {
    toast.error(`Export failed: ${outcome.error}`);
    return;
  }

  // macOS reports success without a path; don't turn that into a failure.
  toast.success(
    outcome.path
      ? `Saved to ${outcome.path}`
      : 'Download complete (this platform does not report the saved path)'
  );
}

/**
 * Export a conversation as a Markdown file download.
 */
export function exportConversationAsMarkdown(
  conversationId: string,
  name: string,
  messages: Message[],
  options?: ExportMarkdownOptions
): DownloadOutcome | Promise<DownloadOutcome> {
  const markdown = formatConversationAsMarkdown(name, messages, options);
  // Sanitize filename: replace unsafe characters with dashes
  const safeName = (name || conversationId)
    .replace(/[^a-zA-Z0-9_\-. ]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 100);
  return downloadAsFile(markdown, `${safeName}.md`);
}

/**
 * Export a conversation as a JSON file download.
 */
export function exportConversationAsJSON(
  conversationId: string,
  name: string,
  messages: Message[]
): DownloadOutcome | Promise<DownloadOutcome> {
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
  return downloadAsFile(json, `${safeName}.json`, 'application/json');
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

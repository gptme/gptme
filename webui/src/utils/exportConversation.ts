import { toast } from 'sonner';
import type { Message } from '@/types/conversation';
import { isTauriEnvironment } from '@/utils/tauri';

export interface ExportMarkdownOptions {
  includeSystem?: boolean;
  includeTimestamps?: boolean;
  /**
   * When false, strip actual assistant `<think>` / `<thinking>` / `<think redacted>`
   * blocks. Literal examples in fenced or inline code are kept. Top-level tags
   * that look like thinking are treated as real (ambiguous by design).
   */
  includeThinking?: boolean;
  /**
   * When false, omit `role: 'tool'` messages and strip actual assistant tool
   * fences / `@tool: {...}` calls. Literal examples in non-tool fences or
   * inline code are kept. Top-level tool syntax is treated as real. This is a
   * display filter, not a secret-redaction guarantee.
   */
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

interface CharRange {
  start: number;
  end: number;
}

interface FenceRange extends CharRange {
  tag: string;
}

// gptme convention (see processNestedCodeBlocks): ```lang is always an opener,
// a bare ``` is always a closer. Nested fences are part of the outer block.
const FENCE_LINE_RE = /^(\s*)(`{3,}|~{3,})(.*)$/;
const THINK_OPEN_RE = /^<think(?:ing)?(?: redacted)?>/;
const THINK_CLOSE_RE = /^<\/think(?:ing)?(?: redacted)?>/;
const TOOL_CALL_PREFIX_RE = /^@[\w.-]+(?:\([^)\n]*\))?:\s*/;

function splitLines(content: string): { lines: string[]; starts: number[] } {
  const lines = content.split('\n');
  const starts: number[] = [];
  let offset = 0;
  for (let i = 0; i < lines.length; i++) {
    starts.push(offset);
    offset += lines[i].length + (i < lines.length - 1 ? 1 : 0);
  }
  return { lines, starts };
}

function lineEnd(lines: string[], starts: number[], index: number): number {
  return starts[index] + lines[index].length + (index < lines.length - 1 ? 1 : 0);
}

function unquoteLine(line: string, maxDepth = Infinity): { text: string; depth: number } {
  let depth = 0;
  while (depth < maxDepth) {
    const prefix = line.match(/^ {0,3}>[\t ]?/);
    if (!prefix) break;
    line = line.slice(prefix[0].length);
    depth++;
  }
  return { text: line, depth };
}

/**
 * Top-level fenced blocks, including quoted examples. Unclosed fences extend
 * to the end of `content` or their quote container, whichever comes first.
 */
function getTopLevelFenceRanges(content: string): FenceRange[] {
  const { lines, starts } = splitLines(content);
  const ranges: FenceRange[] = [];
  let i = 0;
  while (i < lines.length) {
    const { text, depth: quoteDepth } = unquoteLine(lines[i]);
    const match = text.match(FENCE_LINE_RE);
    if (!match || (quoteDepth > 0 && !/^ {0,3}$/.test(match[1]))) {
      i++;
      continue;
    }
    const start = starts[i];
    const rawTag = match[3].trim();
    // Tilde fences are Markdown examples, not gptme tool delimiters.
    const tilde = match[2][0] === '~';
    // Quoted tool-looking fences are examples, never executable top-level tools.
    const tag = tilde || quoteDepth ? '' : (rawTag.split(/\s+/)[0]?.toLowerCase() ?? '');
    const fenceLengths = [match[2].length];
    i++;
    let closed = false;
    while (i < lines.length) {
      const { text: innerLine, depth: innerDepth } = unquoteLine(lines[i], quoteDepth);
      if (innerDepth < quoteDepth) {
        // Fenced blocks cannot lazily continue outside their quote container.
        ranges.push({ start, end: starts[i], tag });
        closed = true;
        break;
      }
      const inner = innerLine.match(FENCE_LINE_RE);
      if (inner && inner[2][0] === match[2][0]) {
        if (tilde) {
          // CommonMark tilde fences do not nest; only a bare run at least
          // as long as the opener closes the block.
          if (!inner[3].trim() && inner[2].length >= match[2].length) {
            ranges.push({ start, end: lineEnd(lines, starts, i), tag });
            i++;
            closed = true;
            break;
          }
        } else if (inner[3].trim()) {
          fenceLengths.push(inner[2].length);
        } else if (inner[2].length >= fenceLengths[fenceLengths.length - 1]) {
          // A shorter bare run is literal content, not the current closer.
          fenceLengths.pop();
          if (fenceLengths.length === 0) {
            ranges.push({ start, end: lineEnd(lines, starts, i), tag });
            i++;
            closed = true;
            break;
          }
        }
      }
      i++;
    }
    if (!closed) {
      ranges.push({ start, end: content.length, tag });
    }
  }
  return ranges;
}

function rangeContains<T extends CharRange>(ranges: T[], index: number): T | undefined {
  return ranges.find((range) => index >= range.start && index < range.end);
}

function matchAt(content: string, index: number, re: RegExp): string | null {
  const match = content.slice(index).match(re);
  return match ? match[0] : null;
}

function isBackslashEscaped(content: string, index: number): boolean {
  let backslashes = 0;
  for (let i = index - 1; i >= 0 && content[i] === '\\'; i--) backslashes++;
  return backslashes % 2 !== 0;
}

/**
 * End offset of a CommonMark-style inline code span starting at `start`,
 * or null if the backtick run is unclosed. A span cannot cross a fenced
 * block or paragraph boundary. Quote prefixes belong to the container rather
 * than the inline content, so they do not interrupt a span inside that quote.
 */
function findInlineCodeEnd(
  content: string,
  start: number,
  fenceRanges: CharRange[]
): number | null {
  // Backslash escapes apply in prose, but not to closers inside a code span.
  if (content[start] !== '`' || isBackslashEscaped(content, start)) return null;
  let openLen = 1;
  while (start + openLen < content.length && content[start + openLen] === '`') openLen++;
  const lineStart = content.lastIndexOf('\n', start - 1) + 1;
  const quoteDepth = unquoteLine(content.slice(lineStart, start)).depth;
  let j = start + openLen;
  while (j < content.length) {
    if (rangeContains(fenceRanges, j)) return null;
    if (content[j] === '\n') {
      const lineEnd = content.indexOf('\n', j + 1);
      const rawLine = content.slice(j + 1, lineEnd < 0 ? content.length : lineEnd);
      const { text: nextLine, depth: nextDepth } = unquoteLine(rawLine, quoteDepth);
      // These block starts interrupt a paragraph; a later backtick belongs to
      // a different inline parsing context. Ordered lists interrupt only at 1.
      if (
        /^[\t \r]*$/.test(nextLine) ||
        /^ {0,3}(?:#{1,6}(?:[\t ]|$)|>|[-+*][\t ]|1[.)][\t ])/.test(nextLine) ||
        /^ {0,3}(?:(?:\*[\t ]*){3,}|(?:_[\t ]*){3,}|(?:-[\t ]*){3,})\r?$/.test(nextLine) ||
        (nextDepth === quoteDepth && /^ {0,3}(?:=+|-+)[\t ]*\r?$/.test(nextLine))
      ) {
        return null;
      }
    }
    if (content[j] !== '`') {
      j++;
      continue;
    }
    let closeLen = 1;
    while (j + closeLen < content.length && content[j + closeLen] === '`') closeLen++;
    if (closeLen === openLen) return j + closeLen;
    j += closeLen;
  }
  return null;
}

function skipBacktickRun(content: string, start: number): number {
  // Only the first backtick is escaped; the remainder can open a code span.
  if (isBackslashEscaped(content, start)) return start + 1;
  let i = start;
  while (i < content.length && content[i] === '`') i++;
  return i === start ? start + 1 : i;
}

/**
 * Index just after the closing brace of the JSON object starting at `start`
 * (`content[start]` must be `{`), or null if the object never closes.
 * Tracks strings so backticks and braces inside quotes are not delimiters.
 */
function findJsonObjectEnd(content: string, start: number): number | null {
  if (content[start] !== '{') return null;
  let depth = 0;
  let inString = false;
  let escapeNext = false;
  for (let i = start; i < content.length; i++) {
    const ch = content[i];
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
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return i + 1;
    }
  }
  return null;
}

/**
 * Real thinking blocks in prose. Inline code is recognized only outside
 * thinking, so a backtick inside `<think>` cannot pair with one in the
 * answer and hide `</think>`. Think tags inside a complete inline-code
 * span stay literal examples.
 */
function getThinkingRanges(content: string, fenceRanges: FenceRange[]): CharRange[] {
  const ranges: CharRange[] = [];
  let i = 0;
  while (i < content.length) {
    const fence = rangeContains(fenceRanges, i);
    if (fence) {
      i = fence.end;
      continue;
    }
    if (content[i] === '`') {
      const end = findInlineCodeEnd(content, i, fenceRanges);
      i = end ?? skipBacktickRun(content, i);
      continue;
    }
    // JSON argument strings are tool data, not prose reasoning wrappers.
    // Consume the whole call before inspecting tags inside its arguments.
    if (i === 0 || content[i - 1] === '\n') {
      const prefix = content.slice(i).match(TOOL_CALL_PREFIX_RE);
      if (prefix) {
        const end = findJsonObjectEnd(content, i + prefix[0].length);
        if (end !== null) {
          i = end;
          continue;
        }
      }
    }
    const open = matchAt(content, i, THINK_OPEN_RE);
    if (!open) {
      i++;
      continue;
    }
    let j = i + open.length;
    let closed = false;
    while (j < content.length) {
      // Once real reasoning starts, its wrapper owns the boundary, even if
      // Markdown inside it is unfinished or pairs with fences in the answer.
      const close = matchAt(content, j, THINK_CLOSE_RE);
      if (close) {
        let end = j + close.length;
        if (content[end] === '\n') end++;
        ranges.push({ start: i, end });
        // An unfinished reasoning fence must not protect the answer or later
        // thinking blocks. The answer starts a fresh Markdown context.
        const prefix = end > 0 && content[end - 1] !== '\n' ? 'x' : '';
        fenceRanges = getTopLevelFenceRanges(prefix + content.slice(end)).map((fence) => ({
          ...fence,
          start: fence.start + end - prefix.length,
          end: fence.end + end - prefix.length,
        }));
        i = end;
        closed = true;
        break;
      }
      j++;
    }
    if (!closed) {
      ranges.push({ start: i, end: content.length });
      break;
    }
  }
  return ranges;
}

function removeCharRanges(content: string, ranges: CharRange[]): string {
  if (ranges.length === 0) return content;
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const merged: CharRange[] = [];
  for (const range of sorted) {
    const last = merged[merged.length - 1];
    if (last && range.start <= last.end) {
      last.end = Math.max(last.end, range.end);
    } else {
      merged.push({ start: range.start, end: range.end });
    }
  }
  let result = '';
  let cursor = 0;
  for (const range of merged) {
    result += content.slice(cursor, range.start);
    cursor = range.end;
  }
  result += content.slice(cursor);
  return result;
}

/**
 * Strip thinking/reasoning blocks from assistant message content.
 *
 * Handles `<thinking>` / `<think>` tags and the `<think redacted>` variant
 * gptme emits for Anthropic's RedactedThinkingBlock (see llm_anthropic.py).
 * Also strips a trailing *unclosed* thinking tag: if generation is
 * interrupted mid-thinking, gptme never streams the closing `</think>`, so
 * the reasoning would otherwise leak into the exported "response" content.
 *
 * Literal examples inside fenced code blocks or inline code are not rewritten.
 * A top-level think tag (not fenced and not inline-coded) is treated as real
 * thinking even if it was meant as an example — that case is ambiguous.
 * This is a display filter, not a secret-redaction guarantee.
 */
export function stripThinkingBlocks(content: string): string {
  const fences = getTopLevelFenceRanges(content);
  return removeCharRanges(content, getThinkingRanges(content, fences)).trim();
}

// Built-in gptme tool block types (markdown codeblock langtags that represent
// a tool invocation rather than example code). Mirrors block_types across
// gptme/tools/*.py. view_anchored/patch_anchored were removed from the Python
// tools but remain here so older transcripts still filter.
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

/**
 * Strip top-level tool fences and `@tool(id): {...}` / `@tool: {...}` calls
 * from assistant message content, using original offsets for both forms.
 * Nested tool fences inside examples are kept; unclosed tool fences are removed.
 * Scans braces rather than regex-matching JSON so pretty-printed or compact
 * argument objects are both handled correctly.
 *
 * Calls inside fenced or inline-code examples are left unchanged. A top-level
 * `@tool: {...}` line is treated as a real invocation even if it was meant as
 * an example. JSON arguments are scanned as a whole so backticks inside a
 * string (e.g. `"Use \`x\` now."`) stay part of the call, not inline code.
 */
function stripAtFormatToolCalls(content: string): string {
  const fences = getTopLevelFenceRanges(content);

  const toRemove: CharRange[] = [];
  let i = 0;
  while (i < content.length) {
    const fence = rangeContains(fences, i);
    if (fence) {
      if (fence.tag && isToolBlockTag(fence.tag)) toRemove.push(fence);
      i = fence.end;
      continue;
    }
    const atLineStart = i === 0 || content[i - 1] === '\n';
    if (atLineStart) {
      const match = content.slice(i).match(TOOL_CALL_PREFIX_RE);
      if (match && content[i + match[0].length] === '{') {
        const jsonEnd = findJsonObjectEnd(content, i + match[0].length);
        if (jsonEnd !== null) {
          toRemove.push({ start: i, end: jsonEnd });
          i = jsonEnd;
          continue;
        }
        const nextFence = fences.find((range) => range.start >= i);
        const end = nextFence ? nextFence.start : content.length;
        toRemove.push({ start: i, end });
        i = end;
        continue;
      }
    }
    if (content[i] === '`') {
      const end = findInlineCodeEnd(content, i, fences);
      i = end ?? skipBacktickRun(content, i);
      continue;
    }
    i++;
  }
  return removeCharRanges(content, toRemove);
}

/**
 * Strip tool invocations embedded directly in assistant message content
 * (fenced tool codeblocks and `@tool: {...}` calls). Distinct from filtering
 * `role: 'tool'` result messages, which getExportableMessages handles.
 */
function stripToolInvocations(content: string, includeThinking: boolean): string {
  // Sections reset Markdown context, not physical line context. A harmless
  // prose prefix keeps a mid-line suffix from becoming a call/fence opener.
  const stripSection = (section: string, atLineStart: boolean): string =>
    atLineStart ? stripAtFormatToolCalls(section) : stripAtFormatToolCalls('x' + section).slice(1);
  const isLineStart = (offset: number): boolean => offset === 0 || content[offset - 1] === '\n';
  const thinking = getThinkingRanges(content, getTopLevelFenceRanges(content));
  if (!includeThinking) {
    const sections: string[] = [];
    const lineStarts: boolean[] = [];
    let cursor = 0;
    for (const range of thinking) {
      sections.push(content.slice(cursor, range.start));
      lineStarts.push(isLineStart(cursor));
      cursor = range.end;
    }
    sections.push(content.slice(cursor));
    lineStarts.push(isLineStart(cursor));
    // Preserve stripThinkingBlocks' outer trimming before tool removal, but
    // never parse across a deleted reasoning section's Markdown boundary.
    for (let i = 0; i < sections.length; i++) {
      const trimmed = sections[i].trimStart();
      if (sections[i].slice(0, sections[i].length - trimmed.length).includes('\n')) {
        lineStarts[i] = true;
      }
      sections[i] = trimmed;
      if (sections[i]) break;
    }
    for (let i = sections.length - 1; i >= 0; i--) {
      sections[i] = sections[i].trimEnd();
      if (sections[i]) break;
    }
    return sections.map((section, i) => stripSection(section, lineStarts[i])).join('');
  }
  let result = '';
  let cursor = 0;
  for (const range of thinking) {
    result += stripSection(content.slice(cursor, range.start), isLineStart(cursor));
    const block = content.slice(range.start, range.end);
    const open = block.match(THINK_OPEN_RE)![0];
    const close = block.match(/<\/think(?:ing)?(?: redacted)?>\n?$/)?.[0] ?? '';
    // Keep the reasoning wrapper when requested, but parse its Markdown
    // separately so unfinished fences cannot swallow the answer or its tools.
    result +=
      open +
      stripSection(
        block.slice(open.length, block.length - close.length),
        isLineStart(range.start + open.length)
      ) +
      close;
    cursor = range.end;
  }
  return result + stripSection(content.slice(cursor), isLineStart(cursor));
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
 *
 * `includeThinking` / `includeTools` filter actual assistant transcript syntax.
 * They do not rewrite literal examples in fenced or inline code, do not touch
 * user/system content, and do nothing in full mode (both flags true). A
 * top-level think tag or tool fence/call that is not wrapped in a non-tool
 * fence or inline code is treated as real — that case is ambiguous. Interrupted
 * (unclosed) top-level tool fences are stripped. This is not secret redaction
 * and does not change JSON download / full-history fetching.
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
      if (!includeTools) content = stripToolInvocations(content, includeThinking);
      else if (!includeThinking) content = stripThinkingBlocks(content);
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

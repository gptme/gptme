import { marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import { highlightCode } from './highlightUtils';

// Create custom renderer
const renderer = new marked.Renderer();

interface MarkedLink {
  href: string;
  title?: string | null;
  text: string;
}

// Override link rendering to open external links in new tabs
renderer.link = ({ href, title, text }: MarkedLink) => {
  const isExternal = href && (href.startsWith('http://') || href.startsWith('https://'));
  const attrs = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
  const titleAttr = title ? ` title="${title}"` : '';
  return `<a href="${href}"${attrs}${titleAttr}>${text}</a>`;
};

marked.setOptions({
  gfm: true,
  breaks: true,
  silent: true,
  renderer: renderer,
});

marked.use(
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code, lang, info) {
      // Use info for file extension detection if available
      const langFromInfo = info ? info.split('.').pop() : undefined;
      // Use our shared utility
      return highlightCode(code, langFromInfo || lang, true).code;
    },
  })
);

/**
 * Process nested code blocks using the gptme fence convention:
 * - A fence line with a lang tag (` ```lang `) is always an opener
 * - A bare fence line (` ``` `) is always a closer
 *
 * Neither `marked` nor `smd` (streaming markdown) understand this nesting
 * convention — they both close a fence on any matching backtick count.
 * This function widens outer fences (e.g. ``` → ````) so inner fences
 * are treated as content, not fence boundaries.
 *
 * Used in TWO rendering paths:
 * - `parseMarkdownContent()` below (marked-based, used for non-chat rendering)
 * - `ChatMessage.tsx` (smd-based, the main chat message renderer)
 * Both must call this before feeding content to their parser.
 */
export function processNestedCodeBlocks(content: string) {
  const lines = content.split('\n');
  const langtags: string[] = [];
  const fenceRe = /^(\s*)(`{3,})(.*)$/;

  // Parse fences using gptme convention, tracking nesting depth.
  // For each depth-0 block, record its opener/closer line indices and
  // the max backtick length of any inner fence.
  interface Block {
    openerLine: number;
    closerLine: number;
    openerLen: number;
    maxInnerLen: number; // max backtick count of fences inside this block
  }
  const blocks: Block[] = [];
  // Stack of open blocks: [openerLine, openerLen, maxInnerLen]
  const stack: [number, number, number][] = [];

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(fenceRe);
    if (!m) continue;
    const [, , backticks, tag] = m;
    const trimmedTag = tag.trim();
    const len = backticks.length;

    if (stack.length === 0) {
      // Top level: any fence opens a block
      if (trimmedTag) langtags.push(trimmedTag);
      stack.push([i, len, 0]);
    } else if (trimmedTag) {
      // Inside a block: fence with tag = nested opener
      langtags.push(trimmedTag);
      // Update parent's maxInnerLen
      stack[stack.length - 1][2] = Math.max(stack[stack.length - 1][2], len);
      stack.push([i, len, 0]);
    } else {
      // Bare fence = closer
      const [openerLine, openerLen, maxInnerLen] = stack.pop()!;
      // If this closed a nested block, update the parent's maxInnerLen
      if (stack.length > 0) {
        stack[stack.length - 1][2] = Math.max(stack[stack.length - 1][2], len);
      }
      // If this was a depth-0 block, record it
      if (stack.length === 0) {
        blocks.push({ openerLine, closerLine: i, openerLen, maxInnerLen });
      }
    }
  }

  // Build adjustment map: only widen fences that actually contain nested fences
  const adjustments = new Map<number, number>();
  for (const block of blocks) {
    if (block.maxInnerLen > 0) {
      const needed = Math.max(block.maxInnerLen + 1, block.openerLen);
      if (needed > block.openerLen) {
        adjustments.set(block.openerLine, needed);
        adjustments.set(block.closerLine, needed);
      }
    }
  }

  // Emit lines with adjusted backtick counts
  const result: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const adj = adjustments.get(i);
    if (adj) {
      const m = lines[i].match(fenceRe)!;
      const [, indent, , tag] = m;
      result.push(`${indent}${'`'.repeat(adj)}${tag}`);
    } else {
      result.push(lines[i]);
    }
  }

  return {
    processedContent: result.join('\n'),
    langtags: langtags.filter(Boolean),
  };
}

export function transformThinkingTags(content: string) {
  if (content.startsWith('`') && content.endsWith('`')) {
    return content;
  }

  return content.replace(
    /<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/g,
    (_match: string, thinkingContent: string) =>
      `<details type="thinking"><summary>💭 Thinking</summary>\n\n${thinkingContent}\n\n</details>`
  );
}

export function parseMarkdownContent(content: string) {
  const processedContent = transformThinkingTags(content);
  const { processedContent: transformedContent, langtags } =
    processNestedCodeBlocks(processedContent);

  let parsedResult = marked.parse(transformedContent, {
    async: false,
  });

  parsedResult = parsedResult.replace(
    /<pre><code(?:\s+class="([^"]+)")?>([^]*?)<\/code><\/pre>/g,
    (_, classes = '', code) => {
      const langtag_fallback = ((classes || '').split(' ')[1] || 'Code').replace('language-', '');
      const langtag = langtags?.shift() || langtag_fallback;
      const emoji = getCodeBlockEmoji(langtag);
      return `
            <details>
                <summary>${emoji} ${langtag}</summary>
                <pre><code class="${classes}">${code}</code></pre>
            </details>
            `;
    }
  );

  return parsedResult;
}

export function getCodeBlockEmoji(langtag: string): string {
  if (isPath(langtag)) return '📄';
  if (isTool(langtag)) return '🛠️';
  if (isOutput(langtag)) return '📤';
  if (isWrite(langtag)) return '📝';
  return '💻';
}

function isPath(langtag: string): boolean {
  return (
    (langtag.includes('/') || langtag.includes('\\') || langtag.includes('.')) &&
    langtag.split(' ').length === 1
  );
}

function isTool(langtag: string): boolean {
  return ['ipython', 'shell', 'tmux'].includes(langtag.split(' ')[0].toLowerCase());
}

function isOutput(langtag: string): boolean {
  return ['stdout', 'stderr', 'result'].includes(langtag.toLowerCase());
}

function isWrite(langtag: string): boolean {
  return ['save', 'patch', 'append'].includes(langtag.split(' ')[0].toLowerCase());
}

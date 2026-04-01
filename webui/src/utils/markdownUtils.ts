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

  // Parse fences using gptme convention and record ALL opener-closer pairs
  // at every nesting depth (not just depth-0).
  interface Block {
    openerLine: number;
    closerLine: number;
    openerLen: number; // actual backtick count of this block's opener
    depth: number;
  }
  const blocks: Block[] = [];
  // Stack: [openerLine, depth, openerLen]
  const stack: [number, number, number][] = [];

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(fenceRe);
    if (!m) continue;
    const [, , backticks, tag] = m;
    const len = backticks.length;
    const trimmedTag = tag.trim();
    const depth = stack.length;

    if (stack.length === 0) {
      if (trimmedTag) langtags.push(trimmedTag);
      stack.push([i, depth, len]);
    } else if (trimmedTag) {
      // Nested opener
      langtags.push(trimmedTag);
      stack.push([i, depth, len]);
    } else {
      // Closer
      const [openerLine, blockDepth, openerLen] = stack.pop()!;
      blocks.push({ openerLine, closerLine: i, openerLen, depth: blockDepth });
    }
  }

  // Bottom-up: compute the minimum backtick count needed for each block so that
  // inner fences (after widening) are never mistaken as the outer closer.
  // A block must be strictly wider than its widest direct child.
  // Process deepest blocks first so children's final counts are known.
  const neededLen = new Map<number, number>(); // openerLine → needed backtick count
  const sortedBlocks = [...blocks].sort((a, b) => b.depth - a.depth);
  for (const block of sortedBlocks) {
    // Direct children: blocks at depth+1 contained within this block
    const children = blocks.filter(
      (c) =>
        c.depth === block.depth + 1 &&
        c.openerLine > block.openerLine &&
        c.closerLine < block.closerLine
    );
    let maxChildLen = 0;
    for (const child of children) {
      maxChildLen = Math.max(maxChildLen, neededLen.get(child.openerLine) ?? child.openerLen);
    }
    const needed =
      children.length > 0 ? Math.max(block.openerLen, maxChildLen + 1) : block.openerLen;
    neededLen.set(block.openerLine, needed);
  }

  // Apply adjustments where the needed count exceeds the original.
  const adjustments = new Map<number, number>();
  for (const block of blocks) {
    const needed = neededLen.get(block.openerLine) ?? block.openerLen;
    if (needed > block.openerLen) {
      adjustments.set(block.openerLine, needed);
      adjustments.set(block.closerLine, needed);
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

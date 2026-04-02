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
    depth: number;
    maxDescendantDepth: number; // deepest nesting level inside this block
  }
  const blocks: Block[] = [];
  // Stack: [openerLine, depth, maxDescendantDepth]
  const stack: [number, number, number][] = [];

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(fenceRe);
    if (!m) continue;
    const [, , , tag] = m;
    const trimmedTag = tag.trim();
    const depth = stack.length;

    if (stack.length === 0) {
      if (trimmedTag) langtags.push(trimmedTag);
      stack.push([i, depth, depth]);
    } else if (trimmedTag) {
      // Nested opener
      langtags.push(trimmedTag);
      // Update ancestor maxDescendantDepth
      for (const frame of stack) {
        frame[2] = Math.max(frame[2], depth);
      }
      stack.push([i, depth, depth]);
    } else {
      // Closer
      const [openerLine, blockDepth, maxDescendantDepth] = stack.pop()!;
      blocks.push({ openerLine, closerLine: i, depth: blockDepth, maxDescendantDepth });
    }
  }

  // Widen fences: each block's backtick count = 3 + (maxDescendantDepth - depth)
  // so inner fences are always shorter than outer fences.
  const adjustments = new Map<number, number>();
  for (const block of blocks) {
    const nestingBelow = block.maxDescendantDepth - block.depth;
    if (nestingBelow > 0) {
      const needed = 3 + nestingBelow;
      const current = lines[block.openerLine].match(fenceRe)![2].length;
      if (needed > current) {
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

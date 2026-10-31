import { marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import { highlightCode } from './highlightUtils';
import { scanMarkdownFences } from './markdownFences';

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
  const fenceRe = /^(\s*)(`{3,})(.*)$/;
  const blocks = scanMarkdownFences(content);
  const langtags = blocks.map((block) => block.tag);

  // Work from descendants outward so even longer tagged inner fences fit
  // beneath a strictly wider outer fence in both Markdown rendering paths.
  const adjustments = new Map<number, number>();
  const childWidths: number[] = [];
  for (const block of [...blocks].reverse()) {
    if (block.marker[0] !== '`') continue;
    const needed = Math.max(block.marker.length, (childWidths[block.depth + 1] ?? 0) + 1);
    childWidths[block.depth + 1] = 0;
    childWidths[block.depth] = Math.max(childWidths[block.depth] ?? 0, needed);
    if (needed > block.marker.length && block.closerLine !== null) {
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
      // Code-block emoji — NON-CHAT renderer (marked, HTML string output).
      // This builds an HTML string, so the <summary> can carry inline markup
      // (styled spans, etc.). The CHAT view does NOT come through here — it uses
      // the smd custom renderer in markdownRenderer.ts, which builds DOM nodes
      // via .textContent and can't carry HTML. Any visual tweak to the summary
      // (e.g. styling the emoji) must be applied in BOTH places to be consistent.
      const emoji = getCodeBlockEmoji(langtag);
      // Scanner tags are original source text, not marked's escaped metadata.
      const label = langtag.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return `
            <details>
                <summary>${emoji} ${label}</summary>
                <pre><code class="${classes}">${code}</code></pre>
            </details>
            `;
    }
  );

  return parsedResult;
}

export function getCodeBlockEmoji(langtag: string | undefined): string {
  if (!langtag) return '💻';
  // TODO: replace these with proper icons
  if (isPath(langtag)) return '📄';
  if (isTool(langtag)) return '🛠️';
  if (isOutput(langtag)) return '📤';
  if (isWrite(langtag)) return '📝';
  if (isBrowser(langtag)) return '🌐';
  if (isComplete(langtag)) return '🏁';
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

function isBrowser(langtag: string): boolean {
  return ['browser', 'search', 'web'].includes(langtag.toLowerCase());
}

function isComplete(langtag: string): boolean {
  return langtag.toLowerCase() === 'complete';
}

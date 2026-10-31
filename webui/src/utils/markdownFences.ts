export interface MarkdownFence {
  openerLine: number;
  closerLine: number | null;
  closerWidth: number | null;
  start: number;
  end: number;
  tag: string;
  marker: string;
  depth: number;
  maxDescendantDepth: number;
}

/**
 * Source ranges for the transcript's tagged-opener / bare-closer convention.
 * Shorter fences inside a longer fence are literal, as in Markdown examples.
 * Same-length or longer tagged backtick fences nest; tilde fences use Markdown closing
 * rules. Open fences extend to EOF. Nothing here rewrites the source bytes.
 * firstOnly requires a fence at startOffset and stops at its outer closer;
 * offsets are absolute, while line numbers are relative to startOffset.
 * quoteDepth strips that many enclosing quote markers for matching only;
 * a missing marker ends an interrupted fence before the outside line.
 */
export function scanMarkdownFences(
  content: string,
  firstOnly = false,
  startOffset = 0,
  quoteDepth = 0
): MarkdownFence[] {
  const blocks: MarkdownFence[] = [];
  const stack: MarkdownFence[] = [];
  let offset = startOffset;
  let lineNumber = 0;
  const quotePrefix = new RegExp(`^(?: {0,3}>[ \\t]?){${quoteDepth}}`);
  while (offset < content.length) {
    const newline = content.indexOf('\n', offset);
    const lineEnd = newline === -1 ? content.length : newline;
    const nextOffset = newline === -1 ? content.length : newline + 1;
    let line = content.slice(offset, lineEnd);
    if (quoteDepth) {
      const prefix = line.match(quotePrefix)?.[0];
      // Fenced blocks cannot lazily continue outside their enclosing quote.
      // Preserve absolute source ranges, including the original quote markers.
      if (!prefix) {
        for (const frame of stack) frame.end = offset;
        break;
      }
      line = line.slice(prefix.length);
    }
    const match = line.match(/^[ \t]*(`{3,}|~{3,})([^\r\n]*)\r?$/);
    // Backtick info strings cannot contain backticks. In particular, a
    // line-start ```inline span``` must reach inline parsing, not hide EOF.
    if (match && !(match[1][0] === '`' && match[2].includes('`'))) {
      const marker = match[1];
      const tag = match[2].trim();
      const parent = stack.at(-1);
      const sameMarker = parent && marker[0] === parent.marker[0];
      if (parent && sameMarker && !tag && marker.length >= parent.marker.length) {
        parent.closerLine = lineNumber;
        parent.closerWidth = marker.length;
        parent.end = nextOffset;
        stack.pop();
        if (firstOnly && stack.length === 0) break;
      } else if (
        !parent ||
        (sameMarker && marker[0] === '`' && marker.length >= parent.marker.length && tag)
      ) {
        const depth = stack.length;
        for (const frame of stack) {
          frame.maxDescendantDepth = Math.max(frame.maxDescendantDepth, depth);
        }
        const block: MarkdownFence = {
          openerLine: lineNumber,
          closerLine: null,
          closerWidth: null,
          start: offset,
          end: content.length,
          tag,
          marker,
          depth,
          maxDescendantDepth: depth,
        };
        blocks.push(block);
        stack.push(block);
      }
    }
    if (firstOnly && blocks.length === 0) break;
    offset = nextOffset;
    lineNumber++;
  }
  return blocks;
}

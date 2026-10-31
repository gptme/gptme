export interface MarkdownFence {
  openerLine: number;
  closerLine: number | null;
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
 */
export function scanMarkdownFences(content: string, firstOnly = false): MarkdownFence[] {
  const blocks: MarkdownFence[] = [];
  const stack: MarkdownFence[] = [];
  let offset = 0;
  for (const [lineNumber, line] of content.split('\n').entries()) {
    const match = line.match(/^[ \t]*(`{3,}|~{3,})([^\r\n]*)\r?$/);
    if (match) {
      const marker = match[1];
      const tag = match[2].trim();
      const parent = stack.at(-1);
      const sameMarker = parent && marker[0] === parent.marker[0];
      if (parent && sameMarker && !tag && marker.length >= parent.marker.length) {
        parent.closerLine = lineNumber;
        parent.end = Math.min(content.length, offset + line.length + 1);
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
    offset += line.length + 1;
  }
  return blocks;
}

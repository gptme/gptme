type Container = { kind: 'quote' } | { kind: 'list'; indent: number };

/** Strip indentation by columns for matching only; callers retain source bytes. */
function indentation(
  line: string,
  columns: number,
  initialColumn = 0
): { text: string; width: number } {
  let width = 0;
  let i = 0;
  while (i < line.length && width < columns && (line[i] === ' ' || line[i] === '\t')) {
    width += line[i] === '\t' ? 4 - ((initialColumn + width) % 4) : 1;
    i++;
  }
  return { text: ' '.repeat(Math.max(0, width - columns)) + line.slice(i), width };
}

function continueContainer(line: string, container: Container): string | null {
  if (container.kind === 'quote') {
    const prefix = line.match(/^ {0,3}>[ \t]?/);
    return prefix ? line.slice(prefix[0].length) : null;
  }
  if (/^[ \t\r]*$/.test(line)) return '';
  const stripped = indentation(line, container.indent);
  return stripped.width >= container.indent ? stripped.text : null;
}

/**
 * A line-local matching view, never an export rewrite. Lists remember their
 * actual item indentation; fences cannot lazily continue outside a container.
 * Construct views only as the forward export scan reaches a line, not over
 * opaque retained reasoning or JSON payloads.
 */
export class MarkdownContainers {
  private containers: Container[] = [];

  enter(line: string, inParagraph = false): { text: string; contained: boolean; changed: boolean } {
    let text = line;
    let retained = 0;
    for (const container of this.containers) {
      const next = continueContainer(text, container);
      if (next === null) break;
      text = next;
      retained++;
    }
    let changed = retained !== this.containers.length;
    this.containers.length = retained;
    while (true) {
      const quote = text.match(/^ {0,3}>[ \t]?/);
      if (quote) {
        this.containers.push({ kind: 'quote' });
        text = text.slice(quote[0].length);
        changed = true;
        continue;
      }
      // CommonMark item padding is 1–4 columns. Excess padding leaves an
      // indented-code block after the single required separator.
      const marker = text.match(/^( {0,3})(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)/);
      if (!marker || /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})\r?$/.test(text))
        break;
      // Only an ordered item numbered 1 may interrupt an existing paragraph.
      if (inParagraph && !changed && /^ *\d/.test(marker[0]) && !/^ *1[.)]$/.test(marker[0])) break;
      const after = text.slice(marker[0].length);
      const padding = indentation(after, 5, marker[0].length);
      const width = padding.width > 4 ? 1 : Math.max(1, padding.width);
      const stripped = indentation(after, width, marker[0].length);
      this.containers.push({ kind: 'list', indent: marker[0].length + width });
      text = stripped.text;
      changed = true;
    }
    return { text, contained: this.containers.length > 0, changed };
  }

  /** Snapshot the current container: later sibling items must not join a fence. */
  continuation(): (line: string) => string | null {
    const containers = [...this.containers];
    return (line) => {
      let text = line;
      for (const container of containers) {
        const next = continueContainer(text, container);
        if (next === null) return null;
        text = next;
      }
      return text;
    };
  }
}

/** Source endpoint of an indented code run, including internal blank lines. */
export function indentedCodeEnd(
  content: string,
  start: number,
  firstLine: string,
  continuation: (line: string) => string | null
): number | null {
  if (/^[ \t\r]*$/.test(firstLine) || indentation(firstLine, 4).width < 4) return null;
  let end = start;
  let offset = start;
  while (offset < content.length) {
    const newline = content.indexOf('\n', offset);
    const next = newline === -1 ? content.length : newline + 1;
    const line =
      offset === start
        ? firstLine
        : continuation(content.slice(offset, newline === -1 ? next : newline));
    if (line === null) break;
    if (!/^[ \t\r]*$/.test(line)) {
      if (indentation(line, 4).width < 4) break;
      end = next;
    }
    offset = next;
  }
  return end;
}

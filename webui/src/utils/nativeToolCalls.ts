import { scanMarkdownFences } from './markdownFences';
import { isNativeToolName } from './toolCallParser';

export interface NativeToolCall {
  start: number;
  end: number;
  tool: string;
  json: string;
  complete: boolean;
}

/** Native transcript syntax, excluding literal examples inside fenced blocks. */
export function nativeToolCalls(content: string): NativeToolCall[] {
  const fences = scanMarkdownFences(content).filter((fence) => fence.depth === 0);
  const header = /^@([\w.-]+)\([^\n)]+\):[ \t]*(?=\{)/gm;
  const calls: NativeToolCall[] = [];
  let match: RegExpExecArray | null;
  while ((match = header.exec(content))) {
    if (fences.some((fence) => match!.index >= fence.start && match!.index < fence.end)) continue;
    // Prose like `@mention(bob): {"text":"hi"}` matches the syntax; MCP names
    // keep working via the dotted `server.tool` convention.
    if (!isNativeToolName(match[1])) continue;
    const start = header.lastIndex;
    let depth = 0;
    let quoted = false;
    let escaped = false;
    let end = content.length;
    let complete = false;
    for (let i = start; i < content.length; i++) {
      const ch = content[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (ch === '\\') escaped = true;
        else if (ch === '"') quoted = false;
      } else if (ch === '"') quoted = true;
      else if (ch === '{') depth++;
      else if (ch === '}' && --depth === 0) {
        end = i + 1;
        complete = true;
        break;
      }
    }
    calls.push({
      start: match.index,
      end,
      tool: match[1],
      json: content.slice(start, end),
      complete,
    });
    header.lastIndex = end;
  }
  return calls;
}

/** Render-only conversion. Never alter the stored transcript or executed args. */
export function renderNativeToolCalls(content: string, streaming = false): string {
  if (streaming) {
    // The backend can flush a header before its opening JSON brace arrives.
    // Hold this suffix out of prose instead of flashing a long call ID.
    content = content.replace(/(?:^|\n)@[\w.-]+(?:\([^\n)]*(?:\):?[ \t]*)?)?$/, '');
  }
  let output = '';
  let offset = 0;
  for (const call of nativeToolCalls(content)) {
    output += content.slice(offset, call.start);
    // A payload can contain arbitrary backticks (file contents, patches, etc.).
    const runs = call.json.match(/`+/g) ?? [];
    const fence = '`'.repeat(Math.max(3, ...runs.map((run) => run.length + 1)));
    output += `${fence}tool-call ${call.tool}\n${call.json}${call.complete ? `\n${fence}\n` : ''}`;
    offset = call.end;
  }
  return output + content.slice(offset);
}

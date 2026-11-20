import { nativeToolCalls, renderNativeToolCalls } from '../nativeToolCalls';
import { parseToolCalls, getToolSummary } from '../toolCallParser';

describe('native tool transcripts', () => {
  it('parses escaped JSON and multiple calls, including MCP tools', () => {
    const text =
      '@shell(call_1): {"command":"echo \\"}\\""}\n@goldfish.search_memory(call_2): {"query":"gptme"}';
    const calls = nativeToolCalls(text);
    expect(calls).toHaveLength(2);
    expect(calls.every((call) => call.complete)).toBe(true);
    const parsed = parseToolCalls(text);
    expect(parsed.map((call) => call.tool)).toEqual(['shell', 'goldfish.search_memory']);
    expect(getToolSummary(parsed[1])).toBe('gptme');
  });

  it('holds partial headers while streaming, but not ordinary annotations', () => {
    expect(renderNativeToolCalls('Reply.\n@shell(call_123456', true)).toBe('Reply.\n');
    expect(renderNativeToolCalls('Reply.\n@docs/team.search(call_1', true)).toBe('Reply.\n');
    expect(renderNativeToolCalls('@note: {"important":true}', true)).toBe(
      '@note: {"important":true}'
    );
    expect(renderNativeToolCalls('Thanks.\n@alice please look', true)).toBe(
      'Thanks.\n@alice please look'
    );
  });

  it('leaves unknown bare @name(id) JSON as prose, not a tool call', () => {
    const text = 'Hello @mention(bob): {"text":"hi"} there';
    expect(nativeToolCalls(text)).toEqual([]);
    expect(renderNativeToolCalls(text)).toBe(text);
    expect(parseToolCalls(text)).toEqual([]);
  });

  it('parses MCP names with slashes, spaces, colons, and parentheses', () => {
    const names = ['docs/team.search', 'docs team.search', 'docs:team.search', 'docs(team).search'];
    for (const name of names) {
      const text = `@${name}(call_1): {"query":"hello"}`;
      const calls = nativeToolCalls(text);
      expect(calls).toHaveLength(1);
      expect(calls[0]).toMatchObject({ tool: name, complete: true });
      expect(parseToolCalls(text).map((call) => call.tool)).toEqual([name]);
      expect(renderNativeToolCalls(text)).toContain(`\`\`\`tool-call ${name}`);
      expect(renderNativeToolCalls(text)).not.toContain(`@${name}(call_1)`);
    }
  });

  it('keeps held streaming output an append-only extension at every split', () => {
    // A non-prefix change makes ChatMessage rebuild the parser mid-stream,
    // which re-parses (and briefly drops) the painted reply tail (#5023).
    const full = 'Reply ends here.\n@mcp.search_memory(toolu_01HzVq8PkW3):  {"query":"a \\"b\\""}';
    const prose = 'Reply ends here.\n';
    const brace = full.indexOf('{');
    let previous = '';
    for (let i = 0; i <= full.length; i++) {
      const rendered = renderNativeToolCalls(full.slice(0, i), true);
      expect(rendered.startsWith(previous)).toBe(true);
      // Check the entire output, including the lone '@' and partial call ID:
      // a narrow marker regex could miss either leak (or a blank rendering).
      const expected =
        i <= prose.length
          ? full.slice(0, i)
          : i <= brace
            ? prose
            : `${prose}\`\`\`tool-call mcp.search_memory\n${full.slice(brace, i)}${i === full.length ? '\n```\n' : ''}`;
      expect(rendered).toBe(expected);
      previous = rendered;
    }
  });

  it('ignores examples in markdown fences', () => {
    const example = '```text\n@shell(call_1): {"command":"not executed"}\n```';
    expect(nativeToolCalls(example)).toEqual([]);
    expect(renderNativeToolCalls(example)).toBe(example);
  });

  it('wraps incomplete calls without a closing fence so arguments can stream', () => {
    const prefix = 'Inspecting.\n@shell(call_1): {"command":"echo';
    expect(renderNativeToolCalls(prefix)).toBe('Inspecting.\n```tool-call shell\n{"command":"echo');
    expect(parseToolCalls(prefix)).toEqual([]);
    const complete = renderNativeToolCalls(prefix + ' hello"}');
    expect(complete).toContain('```tool-call shell\n{"command":"echo hello"}\n```');
  });

  it('uses a safe fence for backticks inside file payloads', () => {
    const text = '@save(call_1): {"path":"/tmp/x","content":"```python\\nx=1\\n```"}';
    expect(renderNativeToolCalls(text)).toContain('````tool-call save');
    expect(parseToolCalls(text)[0].kwargs?.path).toBe('/tmp/x');
  });
});

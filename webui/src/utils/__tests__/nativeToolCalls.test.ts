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
    expect(renderNativeToolCalls('Reply.\n@shell(call_123456', true)).toBe('Reply.');
    expect(renderNativeToolCalls('@note: {"important":true}')).toBe('@note: {"important":true}');
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

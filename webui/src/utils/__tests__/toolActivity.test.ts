import { buildToolActivity } from '../toolActivity';
import type { Message } from '@/types/conversation';

function msg(content: string, role: Message['role'] = 'assistant', ts?: string): Message {
  return { role, content, timestamp: ts };
}

describe('buildToolActivity', () => {
  it('returns empty for no messages', () => {
    expect(buildToolActivity([])).toEqual([]);
  });

  it('returns empty when no tool calls exist', () => {
    const messages = [msg('Hello world', 'user'), msg('Sure, I can help.', 'assistant')];
    expect(buildToolActivity(messages)).toEqual([]);
  });

  it('ignores non-gptme code blocks', () => {
    const messages = [
      msg('```typescript\nconst x = 1;\n```', 'assistant'),
      msg('```json\n{"a": 1}\n```', 'assistant'),
    ];
    expect(buildToolActivity(messages)).toEqual([]);
  });

  it('detects a bash tool call', () => {
    const messages = [msg('```bash\nls -la\n```', 'assistant', '2026-08-01T00:00:00Z')];
    const result = buildToolActivity(messages);
    expect(result).toHaveLength(1);
    expect(result[0].tool).toBe('bash');
    expect(result[0].callCount).toBe(1);
    expect(result[0].lastCall.content).toBe('ls -la\n');
  });

  it('detects save tool call with filename arg', () => {
    const messages = [msg('```save myfile.py\nprint("hello")\n```', 'assistant')];
    const result = buildToolActivity(messages);
    expect(result).toHaveLength(1);
    expect(result[0].tool).toBe('save');
    expect(result[0].lastCall.args).toEqual(['myfile.py']);
  });

  it('counts multiple calls to the same tool', () => {
    const messages = [
      msg('```bash\nls\n```', 'assistant'),
      msg('```bash\npwd\n```', 'assistant'),
      msg('```bash\necho hi\n```', 'assistant'),
    ];
    const result = buildToolActivity(messages);
    expect(result).toHaveLength(1);
    expect(result[0].tool).toBe('bash');
    expect(result[0].callCount).toBe(3);
    expect(result[0].lastCall.content).toBe('echo hi\n');
  });

  it('tracks multiple distinct tools', () => {
    const messages = [
      msg('```bash\nls\n```', 'assistant'),
      msg('```python\nprint("hi")\n```', 'assistant'),
      msg('```save out.txt\nhello\n```', 'assistant'),
    ];
    const result = buildToolActivity(messages);
    expect(result).toHaveLength(3);
    const tools = result.map((e) => e.tool);
    expect(tools).toContain('bash');
    expect(tools).toContain('python');
    expect(tools).toContain('save');
  });

  it('sorts by call count descending', () => {
    const messages = [
      msg('```python\nprint(1)\n```', 'assistant'),
      msg('```bash\nls\n```', 'assistant'),
      msg('```bash\npwd\n```', 'assistant'),
    ];
    const result = buildToolActivity(messages);
    expect(result[0].tool).toBe('bash');
    expect(result[0].callCount).toBe(2);
    expect(result[1].tool).toBe('python');
    expect(result[1].callCount).toBe(1);
  });

  it('ignores tool calls in non-assistant messages', () => {
    const messages = [
      msg('```bash\nls\n```', 'user'),
      msg('```bash\npwd\n```', 'system'),
      msg('```bash\necho ok\n```', 'tool'),
    ];
    expect(buildToolActivity(messages)).toHaveLength(0);
  });

  it('handles ipython/shell aliases', () => {
    const messages = [
      msg('```ipython\nimport os\n```', 'assistant'),
      msg('```shell\necho hi\n```', 'assistant'),
    ];
    const result = buildToolActivity(messages);
    expect(result).toHaveLength(2);
    const tools = result.map((e) => e.tool);
    expect(tools).toContain('ipython');
    expect(tools).toContain('shell');
  });

  it('preserves firstSeen timestamp from first call', () => {
    const ts1 = '2026-08-01T00:00:00Z';
    const ts2 = '2026-08-01T01:00:00Z';
    const messages = [
      msg('```bash\nls\n```', 'assistant', ts1),
      msg('```bash\npwd\n```', 'assistant', ts2),
    ];
    const result = buildToolActivity(messages);
    expect(result[0].firstSeen).toBe(ts1);
    expect(result[0].lastCall.timestamp).toBe(ts2);
  });
});

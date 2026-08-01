import { buildToolActivity } from '../toolActivity';
import type { Message } from '@/types/conversation';

function msg(content: string, role: Message['role'] = 'assistant', ts?: string): Message {
  return { role, content, timestamp: ts };
}

const toolResult = (content = 'Tool completed') => msg(content, 'system');

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
      toolResult(),
      msg('```json\n{"a": 1}\n```', 'assistant'),
      toolResult(),
    ];
    expect(buildToolActivity(messages)).toEqual([]);
  });

  it('detects a shell tool call followed by an execution result and continuation', () => {
    const messages = [
      msg('```shell\nls -la\n```', 'assistant', '2026-08-01T00:00:00Z'),
      toolResult(),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity).toHaveLength(1);
    expect(activity[0].tool).toBe('shell');
    expect(activity[0].callCount).toBe(1);
    expect(activity[0].lastCall.content).toBe('ls -la');
  });

  it('detects save tool call with filename arg', () => {
    const messages = [msg('```save myfile.py\nprint("hello")\n```'), toolResult(), msg('Done')];
    const activity = buildToolActivity(messages);
    expect(activity).toHaveLength(1);
    expect(activity[0].tool).toBe('save');
    expect(activity[0].lastCall.args).toEqual(['myfile.py']);
  });

  it('counts multiple calls to the same tool', () => {
    const messages = [
      msg('```shell\nls\n```'),
      toolResult(),
      msg('```shell\npwd\n```'),
      toolResult(),
      msg('```shell\necho hi\n```'),
      toolResult(),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity).toHaveLength(1);
    expect(activity[0].tool).toBe('shell');
    expect(activity[0].callCount).toBe(3);
    expect(activity[0].lastCall.content).toBe('echo hi');
  });

  it('tracks multiple distinct tools', () => {
    const messages = [
      msg('```shell\nls\n```'),
      toolResult(),
      msg('```ipython\nprint("hi")\n```'),
      toolResult(),
      msg('```save out.txt\nhello\n```'),
      toolResult(),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity).toHaveLength(3);
    const tools = activity.map((entry) => entry.tool);
    expect(tools).toContain('shell');
    expect(tools).toContain('ipython');
    expect(tools).toContain('save');
  });

  it('sorts by call count descending', () => {
    const messages = [
      msg('```ipython\nprint(1)\n```'),
      toolResult(),
      msg('```shell\nls\n```'),
      toolResult(),
      msg('```shell\npwd\n```'),
      toolResult(),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity[0].tool).toBe('shell');
    expect(activity[0].callCount).toBe(2);
    expect(activity[1].tool).toBe('ipython');
    expect(activity[1].callCount).toBe(1);
  });

  it('ignores tool calls in non-assistant messages', () => {
    const messages = [
      msg('```shell\nls\n```', 'user'),
      msg('```shell\npwd\n```', 'system'),
      msg('```shell\necho ok\n```', 'tool'),
    ];
    expect(buildToolActivity(messages)).toHaveLength(0);
  });

  it('uses the canonical tool allowlist', () => {
    const messages = [
      msg('```mcp\nlist servers\n```'),
      toolResult(),
      msg('```gh\npr view 1\n```'),
      toolResult(),
      msg('Done'),
    ];
    const tools = buildToolActivity(messages).map((entry) => entry.tool);
    expect(tools).toContain('mcp');
    expect(tools).toContain('gh');
  });

  it('preserves firstSeen timestamp from first call', () => {
    const ts1 = '2026-08-01T00:00:00Z';
    const ts2 = '2026-08-01T01:00:00Z';
    const messages = [
      msg('```shell\nls\n```', 'assistant', ts1),
      toolResult(),
      msg('```shell\npwd\n```', 'assistant', ts2),
      toolResult(),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity[0].firstSeen).toBe(ts1);
    expect(activity[0].lastCall.timestamp).toBe(ts2);
  });

  it('does not count a recognized code example without a tool result', () => {
    const messages = [msg('For example:\n```ipython\nprint("hello")\n```'), msg('Thanks', 'user')];
    expect(buildToolActivity(messages)).toEqual([]);
  });

  it('does not treat a TURN_POST system message as execution evidence', () => {
    const messages = [
      msg('For example:\n```ipython\nprint("hello")\n```'),
      msg('# Relevant Lessons', 'system'),
      msg('Thanks', 'user'),
    ];
    expect(buildToolActivity(messages)).toEqual([]);
  });

  it('counts only calls with corresponding result messages', () => {
    const messages = [
      msg('```shell\nls\n```\n```save out.txt\nhello\n```'),
      toolResult('Ran command'),
      msg('Done'),
    ];
    const activity = buildToolActivity(messages);
    expect(activity).toHaveLength(1);
    expect(activity[0].tool).toBe('shell');
  });
});

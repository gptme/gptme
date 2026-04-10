import { buildStepRoles } from '../stepGrouping';
import type { Message } from '@/types/conversation';

function msg(role: Message['role'], content = ''): Message {
  return { role, content };
}

describe('buildStepRoles', () => {
  const neverHidden = () => false;

  it('returns empty map for no messages', () => {
    expect(buildStepRoles([], neverHidden).size).toBe(0);
  });

  it('returns empty map for simple user-assistant exchange', () => {
    const messages = [msg('user', 'hi'), msg('assistant', 'hello')];
    expect(buildStepRoles(messages, neverHidden).size).toBe(0);
  });

  it('returns empty map when fewer than 2 intermediate steps', () => {
    const messages = [msg('user', 'do something'), msg('assistant', 'done')];
    // Only 0 intermediate steps (single response) — below threshold
    expect(buildStepRoles(messages, neverHidden).size).toBe(0);
  });

  it('groups a single tool call (2 intermediate steps)', () => {
    const messages = [
      msg('user', 'do something'), // 0
      msg('assistant', 'using tool'), // 1 - step
      msg('system', 'saved file'), // 2 - step
      msg('assistant', 'done'), // 3 - response
    ];
    const roles = buildStepRoles(messages, neverHidden);
    expect(roles.get(1)?.type).toBe('group-start');
    expect(roles.get(2)?.type).toBe('grouped');
    expect(roles.get(3)?.type).toBe('response');
  });

  it('groups multiple tool calls (4 intermediate steps) between user messages', () => {
    const messages = [
      msg('user', 'fix the bug'), // 0
      msg('assistant', 'looking at code'), // 1 - step
      msg('system', 'saved file.py'), // 2 - step
      msg('assistant', 'running tests'), // 3 - step
      msg('system', 'All tests passed'), // 4 - step
      msg('assistant', 'Fixed the bug'), // 5 - response
      msg('user', 'thanks'), // 6
    ];

    const roles = buildStepRoles(messages, neverHidden);

    // Index 1 should be group-start
    const start = roles.get(1);
    expect(start).toBeDefined();
    expect(start?.type).toBe('group-start');
    if (start?.type === 'group-start') {
      expect(start.count).toBe(2); // 2 tool calls (system messages at indices 2, 4)
      expect(start.tools).toContain('save');
    }

    // Indices 2, 3, 4 should be grouped
    expect(roles.get(2)?.type).toBe('grouped');
    expect(roles.get(3)?.type).toBe('grouped');
    expect(roles.get(4)?.type).toBe('grouped');

    // Index 5 (last assistant) should be response
    expect(roles.get(5)?.type).toBe('response');
  });

  it('detects tool names from system messages', () => {
    const messages = [
      msg('user', 'do work'),
      msg('assistant', 'step 1'),
      msg('system', 'saved file.py'),
      msg('system', 'Patch applied successfully'),
      msg('system', '$ echo hello\nhello'),
      msg('assistant', 'done'),
    ];

    const roles = buildStepRoles(messages, neverHidden);
    const start = roles.get(1);
    expect(start?.type).toBe('group-start');
    if (start?.type === 'group-start') {
      expect(start.tools).toContain('save');
      expect(start.tools).toContain('patch');
      expect(start.tools).toContain('shell');
    }
  });

  it('skips hidden messages and groups remaining visible steps', () => {
    const messages = [
      msg('user', 'do work'), // 0
      msg('assistant', 'step 1'), // 1
      msg('system', 'hidden'), // 2 - hidden
      msg('system', 'saved'), // 3
      msg('assistant', 'done'), // 4
    ];

    const isHidden = (idx: number) => idx === 2;
    const roles = buildStepRoles(messages, isHidden);
    // 2 visible intermediate steps (1, 3) — meets threshold
    expect(roles.get(1)?.type).toBe('group-start');
    expect(roles.get(3)?.type).toBe('grouped');
    expect(roles.get(4)?.type).toBe('response');
  });

  it('does not group when only 1 visible intermediate step', () => {
    const messages = [
      msg('user', 'do work'), // 0
      msg('assistant', 'step 1'), // 1 - hidden
      msg('system', 'saved'), // 2
      msg('assistant', 'done'), // 3
    ];

    const isHidden = (idx: number) => idx === 1;
    const roles = buildStepRoles(messages, isHidden);
    // Only 1 visible intermediate step — below threshold
    expect(roles.size).toBe(0);
  });

  it('handles multiple user turns independently', () => {
    const messages = [
      msg('user', 'first'), // 0
      msg('assistant', 'a'), // 1
      msg('system', 'b'), // 2
      msg('system', 'c'), // 3
      msg('assistant', 'd'), // 4 - response
      msg('user', 'second'), // 5
      msg('assistant', 'e'), // 6
      msg('assistant', 'f'), // 7 - response (only 1 step before, no group)
    ];

    const roles = buildStepRoles(messages, neverHidden);
    // First turn: 3 steps (1, 2, 3), response at 4
    expect(roles.get(1)?.type).toBe('group-start');
    expect(roles.get(2)?.type).toBe('grouped');
    expect(roles.get(3)?.type).toBe('grouped');
    expect(roles.get(4)?.type).toBe('response');

    // Second turn: only 1 step — no grouping
    expect(roles.has(6)).toBe(false);
    expect(roles.has(7)).toBe(false);
  });

  it('handles no assistant response (all system messages)', () => {
    const messages = [
      msg('user', 'run tests'),
      msg('system', 'output 1'),
      msg('system', 'output 2'),
      msg('system', 'output 3'),
      msg('user', 'ok'),
    ];

    const roles = buildStepRoles(messages, neverHidden);
    // 3 steps, no response — all should be grouped
    const start = roles.get(1);
    expect(start?.type).toBe('group-start');
    if (start?.type === 'group-start') {
      expect(start.count).toBe(3);
    }
    expect(roles.get(2)?.type).toBe('grouped');
    expect(roles.get(3)?.type).toBe('grouped');
  });

  it('response shown when followed by system post-hooks (walking-sad-alien case)', () => {
    // Real scenario: assistant responds, then system adds "Relevant Lessons" etc.
    // The assistant message must NOT be collapsed — it's the actual response.
    const messages = [
      msg('system', 'You are gptme...'), // 0 (hidden initial)
      msg('system', 'Agent Instructions'), // 1 (hidden initial)
      msg('system', 'Selected files'), // 2 (hidden initial)
      msg('system', 'workspace-agents-warning'), // 3
      msg('system', '<budget:token_budget>'), // 4 (hidden)
      msg('user', 'write a fibonacci function'), // 5
      msg('system', '# Relevant Lessons'), // 6 (hidden)
      msg('assistant', 'Here is a fibonacci function:\n```python\ndef fib(n)...```'), // 7 - response
      msg('system', 'Executed code block.\nfib(10) = 55'), // 8
      msg('system', '# Relevant Lessons\n## Git Workflow'), // 9
    ];

    // System messages 0-4, 6 are hidden (initial system or hide flag)
    const isHidden = (idx: number) => idx <= 4 || idx === 6;
    const roles = buildStepRoles(messages, isHidden);

    // Response must be visible, not collapsed
    expect(roles.get(7)?.type).toBe('response');
    // The system messages after it are steps
    expect(roles.get(8)?.type).not.toBe('response');
    expect(roles.get(9)?.type).not.toBe('response');
  });

  it('uses stable group IDs based on message index', () => {
    const messages = [
      msg('user', 'first'), // 0
      msg('assistant', 'a'), // 1
      msg('system', 'b'), // 2
      msg('assistant', 'done'), // 3
      msg('user', 'second'), // 4
      msg('assistant', 'c'), // 5
      msg('system', 'd'), // 6
      msg('assistant', 'done'), // 7
    ];

    const roles = buildStepRoles(messages, neverHidden);
    // Group IDs should be the message index of the first step, not a counter
    const start1 = roles.get(1);
    const start2 = roles.get(5);
    expect(start1?.type).toBe('group-start');
    expect(start2?.type).toBe('group-start');
    if (start1?.type === 'group-start' && start2?.type === 'group-start') {
      expect(start1.groupId).toBe(1); // first step's index
      expect(start2.groupId).toBe(5); // first step's index
      expect(start1.groupId).not.toBe(start2.groupId);
    }
  });

  it('counts tool-call steps (system messages) not raw messages', () => {
    const messages = [
      msg('user', 'do work'), // 0
      msg('assistant', 'thinking'), // 1
      msg('assistant', 'running tool'), // 2
      msg('system', 'tool output'), // 3
      msg('assistant', 'running another'), // 4
      msg('system', 'more output'), // 5
      msg('assistant', 'done'), // 6
    ];

    const roles = buildStepRoles(messages, neverHidden);
    const start = roles.get(1);
    expect(start?.type).toBe('group-start');
    if (start?.type === 'group-start') {
      // 2 system messages = 2 tool-call steps, not 5 raw intermediate messages
      expect(start.count).toBe(2);
    }
  });

  it('last turn without next user message still shows response', () => {
    // Common case: conversation ends mid-turn (no trailing user message)
    const messages = [
      msg('user', 'help me'), // 0
      msg('assistant', 'running tool'), // 1
      msg('system', 'output'), // 2
      msg('assistant', 'Here is the answer'), // 3 - response (last in conversation)
    ];

    const roles = buildStepRoles(messages, neverHidden);
    expect(roles.get(3)?.type).toBe('response');
    expect(roles.get(1)?.type).toBe('group-start');
    expect(roles.get(2)?.type).toBe('grouped');
  });

  it('does not collapse response when runnable fence is followed by non-tool system message', () => {
    // Regression: assistant message includes a runnable fence (e.g. ```shell) as a
    // *suggestion* (the user hasn't run it), followed only by a meta system message.
    // The assistant is the real response and must NOT be absorbed into a step group.
    const messages = [
      msg('user', 'how do I init a git repo?'), // 0
      msg('assistant', 'Run:\n\n```shell\ngit init\n```\n\nThen add a README.'), // 1 - response
      msg('system', '# Relevant Lessons\n## Git Workflow'), // 2 - post-hook, not a tool result
    ];

    const roles = buildStepRoles(messages, neverHidden);
    // Only 1 step after the response — below threshold, no grouping at all
    expect(roles.size).toBe(0);
  });

  it('keeps assistant tool-use messages collapsed when later tool output follows', () => {
    // Regression test for the demo conversation: assistant messages can include prose
    // plus runnable tool blocks. Those are still intermediate steps, not the response.
    const messages = [
      msg('user', 'show me how you can help with Python'), // 0
      msg('assistant', 'First, let\'s create a file:\n\n```save hello.py\nprint("hi")\n```'), // 1
      msg('system', 'Saved to hello.py'), // 2
      msg('assistant', "Now let's run it:\n\n```shell\npython hello.py\n```"), // 3
      msg('system', '```stdout\nhi\n```'), // 4
      msg('assistant', 'We can also use Python interactively:\n\n```ipython\nprint(2 + 2)\n```'), // 5
      msg('system', '```stdout\n4\n```'), // 6
      msg('user', 'thanks'), // 7
    ];

    const roles = buildStepRoles(messages, neverHidden);

    expect(roles.get(1)?.type).toBe('group-start');
    expect(roles.get(2)?.type).toBe('grouped');
    expect(roles.get(3)?.type).toBe('grouped');
    expect(roles.get(4)?.type).toBe('grouped');
    expect(roles.get(5)?.type).toBe('grouped');
    expect(roles.get(6)?.type).toBe('grouped');
    expect(Array.from(roles.values()).some((role) => role.type === 'response')).toBe(false);
  });
});

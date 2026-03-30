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

  it('returns empty map when fewer than 3 intermediate steps', () => {
    const messages = [
      msg('user', 'do something'),
      msg('assistant', 'using tool'),
      msg('system', 'saved file'),
      msg('assistant', 'done'),
    ];
    // Only 2 intermediate steps (assistant "using tool" + system "saved") — below threshold
    expect(buildStepRoles(messages, neverHidden).size).toBe(0);
  });

  it('groups 3+ intermediate steps between user messages', () => {
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
      expect(start.count).toBe(4); // indices 1, 2, 3, 4
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

  it('skips hidden messages', () => {
    const messages = [
      msg('user', 'do work'), // 0
      msg('assistant', 'step 1'), // 1
      msg('system', 'hidden'), // 2 - hidden
      msg('system', 'saved'), // 3
      msg('assistant', 'done'), // 4
    ];

    const isHidden = (idx: number) => idx === 2;
    const roles = buildStepRoles(messages, isHidden);
    // Only 2 visible intermediate steps (1, 3) — below threshold
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
});

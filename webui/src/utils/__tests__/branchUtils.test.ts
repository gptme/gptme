import { computeForkPoints } from '../branchUtils';
import type { Message } from '@/types/conversation';

function msg(role: string, content: string, timestamp: string): Message {
  return { role: role as Message['role'], content, timestamp };
}

describe('computeForkPoints', () => {
  it('returns empty for single branch', () => {
    const branches = {
      main: [msg('system', 'sys', '2024-01-01T00:00:00'), msg('user', 'hi', '2024-01-01T00:01:00')],
    };
    const result = computeForkPoints('main', branches);
    expect(result.size).toBe(0);
  });

  it('returns empty when branches are identical', () => {
    const messages = [
      msg('system', 'sys', '2024-01-01T00:00:00'),
      msg('user', 'hi', '2024-01-01T00:01:00'),
    ];
    const branches = { main: messages, copy: messages };
    const result = computeForkPoints('main', branches);
    expect(result.size).toBe(0);
  });

  it('detects fork when user message was edited', () => {
    const branches = {
      main: [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'edited', '2024-01-01T00:02:00'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'original', '2024-01-01T00:01:00'),
        msg('assistant', 'reply', '2024-01-01T00:01:30'),
      ],
    };
    const result = computeForkPoints('main', branches);
    // Fork at index 0 (system message) — the last common message before divergence at index 1
    expect(result.size).toBe(1);
    expect(result.has(0)).toBe(true);
    const fork = result.get(0)!;
    expect(fork.branchNames).toHaveLength(2);
    expect(fork.branchNames).toContain('main');
    expect(fork.branchNames).toContain('main-edit-0');
  });

  it('shows only ONE fork indicator per branch (not at every subsequent message)', () => {
    const branches = {
      main: [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v2', '2024-01-01T00:02:00'),
        msg('assistant', 'reply-v2', '2024-01-01T00:02:30'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v1', '2024-01-01T00:01:00'),
        msg('assistant', 'reply-v1', '2024-01-01T00:01:30'),
      ],
    };
    const result = computeForkPoints('main', branches);
    // Only ONE fork at the divergence point (index 0), NOT also at index 1
    expect(result.size).toBe(1);
    expect(result.has(0)).toBe(true);
    expect(result.has(1)).toBe(false);
  });

  it('handles multiple edits creating multiple branches at same fork point', () => {
    const branches = {
      main: [msg('system', 'sys', '2024-01-01T00:00:00'), msg('user', 'v3', '2024-01-01T00:03:00')],
      'main-edit-1': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v2', '2024-01-01T00:02:00'),
        msg('assistant', 'reply-v2', '2024-01-01T00:02:30'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v1', '2024-01-01T00:01:00'),
        msg('assistant', 'reply-v1', '2024-01-01T00:01:30'),
      ],
    };
    const result = computeForkPoints('main', branches);
    // All 3 branches diverge at index 1, fork indicator at index 0
    expect(result.size).toBe(1);
    const fork = result.get(0)!;
    expect(fork.branchNames).toHaveLength(3);
    expect(fork.branchNames).toContain('main');
    expect(fork.branchNames).toContain('main-edit-0');
    expect(fork.branchNames).toContain('main-edit-1');
  });

  it('sorts branches by diverging message timestamp (earliest first)', () => {
    const branches = {
      main: [msg('system', 'sys', '2024-01-01T00:00:00'), msg('user', 'v3', '2024-01-01T00:03:00')],
      'main-edit-1': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v2', '2024-01-01T00:02:00'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'v1', '2024-01-01T00:01:00'),
      ],
    };
    const result = computeForkPoints('main', branches);
    const fork = result.get(0)!;
    // Sorted by timestamp: edit-0 (v1, earliest) → edit-1 (v2) → main (v3, latest)
    expect(fork.branchNames[0]).toBe('main-edit-0');
    expect(fork.branchNames[1]).toBe('main-edit-1');
    expect(fork.branchNames[2]).toBe('main');
    expect(fork.currentIndex).toBe(2);
  });

  it('handles forks at different points in conversation', () => {
    // First edit at message 1, second edit at message 3
    const branches = {
      main: [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'q1', '2024-01-01T00:01:00'),
        msg('assistant', 'a1', '2024-01-01T00:01:30'),
        msg('user', 'q2-edited', '2024-01-01T00:03:00'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'q1', '2024-01-01T00:01:00'),
        msg('assistant', 'a1', '2024-01-01T00:01:30'),
        msg('user', 'q2-original', '2024-01-01T00:02:00'),
        msg('assistant', 'a2', '2024-01-01T00:02:30'),
      ],
    };
    const result = computeForkPoints('main', branches);
    // Fork at index 2 (assistant message) — last common before divergence at index 3
    expect(result.size).toBe(1);
    expect(result.has(2)).toBe(true);
  });

  it('handles branch where current is shorter (truncated)', () => {
    const branches = {
      main: [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'edited', '2024-01-01T00:02:00'),
      ],
      'main-edit-0': [
        msg('system', 'sys', '2024-01-01T00:00:00'),
        msg('user', 'original', '2024-01-01T00:01:00'),
        msg('assistant', 'reply', '2024-01-01T00:01:30'),
        msg('user', 'followup', '2024-01-01T00:01:45'),
      ],
    };
    const result = computeForkPoints('main', branches);
    expect(result.size).toBe(1);
    expect(result.has(0)).toBe(true);
    expect(result.get(0)!.branchNames).toHaveLength(2);
  });

  it('returns empty when currentBranch not in branches', () => {
    const branches = { other: [msg('system', 'sys', '2024-01-01T00:00:00')] };
    const result = computeForkPoints('main', branches);
    expect(result.size).toBe(0);
  });

  it('includes identical branches at the same fork point for consistent count', () => {
    // Real scenario: multiple edits where backups were taken at same state
    const shared = [
      msg('system', 'sys', '2024-01-01T00:00:00'),
      msg('user', 'hi', '2024-01-01T00:01:00'),
    ];
    const branches = {
      main: [...shared, msg('assistant', 'reply-v3', '2024-01-01T00:04:00')],
      'main-edit-0': [...shared, msg('assistant', 'reply-v1', '2024-01-01T00:02:00')],
      // edit-1 and edit-2 are identical (backup taken at same truncated state)
      'main-edit-1': [...shared],
      'main-edit-2': [...shared],
    };

    // From any branch, all 4 should be visible at the same fork point
    const fromMain = computeForkPoints('main', branches);
    expect(fromMain.size).toBe(1);
    expect(fromMain.get(1)!.branchNames).toHaveLength(4);

    const fromEdit2 = computeForkPoints('main-edit-2', branches);
    expect(fromEdit2.size).toBe(1);
    // All 4 branches visible (identical branches included at fallback fork index)
    expect(fromEdit2.get(1)!.branchNames).toHaveLength(4);

    const fromEdit1 = computeForkPoints('main-edit-1', branches);
    expect(fromEdit1.size).toBe(1);
    expect(fromEdit1.get(1)!.branchNames).toHaveLength(4);
  });
});

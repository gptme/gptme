import { mergeUserRuns } from '../messageUtils';
import { closeOpenFences } from '../../components/MergedUserMessage';

describe('mergeUserRuns', () => {
  const roles = ['user', 'user', 'assistant', 'user', 'system', 'user', 'user', 'user'];
  const isUser = (index: number) => roles[index] === 'user';

  it('keeps one row per run of user messages, keyed by its first', () => {
    const { rows, runs } = mergeUserRuns([0, 1, 2, 3, 4, 5, 6, 7], isUser);
    expect(rows).toEqual([0, 2, 3, 4, 5]);
    expect([...runs]).toEqual([
      [0, [0, 1]],
      [5, [5, 6, 7]],
    ]);
  });

  it('runs over hidden rows: only the visible ones count', () => {
    // 4 (system) hidden: 3 and 5 become adjacent
    const { rows, runs } = mergeUserRuns([2, 3, 5], isUser);
    expect(rows).toEqual([2, 3]);
    expect(runs.get(3)).toEqual([3, 5]);
  });

  it('keeps a message that is not joinable (e.g. failed) on its own row', () => {
    const failed = new Set([6]);
    const { rows, runs } = mergeUserRuns([5, 6, 7], (index) => isUser(index) && !failed.has(index));
    expect(rows).toEqual([5, 6, 7]);
    expect(runs.size).toBe(0);
  });

  it('ends a run at a message nothing may follow (e.g. a fork point)', () => {
    const { rows, runs } = mergeUserRuns([5, 6, 7], isUser, (index) => index === 6);
    expect(rows).toEqual([5, 7]);
    expect(runs.get(5)).toEqual([5, 6]);
  });
});

describe('closeOpenFences', () => {
  it('leaves balanced content alone', () => {
    const content = 'see\n```python\nprint(1)\n```\ndone';
    expect(closeOpenFences(content)).toBe(content);
  });

  it('closes a fence left open, so the next message is not inside it', () => {
    expect(closeOpenFences('try this:\n```python\nprint(1)')).toBe(
      'try this:\n```python\nprint(1)\n```'
    );
  });

  it('closes nested fences innermost first', () => {
    expect(closeOpenFences('````markdown\n```python\nx')).toBe(
      '````markdown\n```python\nx\n```\n````'
    );
  });
});
